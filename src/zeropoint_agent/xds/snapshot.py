"""Build Envoy xDS snapshots from resolved local and tailnet routes.

Local HTTP endpoints share the public listener on :80 and route by Host at
`/`. Tailnet services enter through a loopback-only listener shared with the
Tailscale runtime. That private listener removes an internal routing prefix and
preserves Tailscale identity headers; public LAN routes remove those headers so
callers cannot spoof tailnet identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import List

import betterproto2
from envoy_data_plane.envoy.config.cluster.v3 import (
    Cluster, ClusterDiscoveryType, ClusterLbPolicy,
)
from envoy_data_plane.envoy.config.core.v3 import (
    Address, AggregatedConfigSource, ApiVersion, ConfigSource, DataSource,
    SocketAddress, SocketAddressProtocol,
)
from envoy_data_plane.envoy.config.endpoint.v3 import (
    ClusterLoadAssignment, Endpoint as EpEndpoint, LbEndpoint,
    LocalityLbEndpoints,
)
from envoy_data_plane.envoy.config.listener.v3 import (
    Filter, FilterChain, Listener,
)
from envoy_data_plane.envoy.config.route.v3 import (
    DirectResponseAction, HeaderMatcher, Route, RouteAction,
    RouteConfiguration, RouteMatch, VirtualHost,
)
from envoy_data_plane.envoy.extensions.filters.http.router.v3 import Router
from envoy_data_plane.envoy.extensions.filters.network.http_connection_manager.v3 import (
    HttpConnectionManager, HttpConnectionManagerCodecType,
    HttpFilter, Rds,
)
from envoy_data_plane.envoy.extensions.filters.network.tcp_proxy.v3 import TcpProxy
from envoy_data_plane.envoy.type.matcher.v3 import StringMatcher
from envoy_data_plane.google.protobuf import Any as PbAny

HTTP_LISTENER_NAME = "http_listener"
HTTP_ROUTES_NAME = "http_routes"
HTTP_LISTEN_PORT = 80
TAILNET_LISTENER_NAME = "tailnet_listener"
TAILNET_ROUTES_NAME = "tailnet_routes"
TAILNET_LISTEN_PORT = 18080

TAILSCALE_IDENTITY_HEADERS = [
    "tailscale-user-login",
    "tailscale-user-name",
    "tailscale-user-profile-pic",
    "tailscale-app-capabilities",
]


@dataclass
class ResolvedEndpoint:
    """An endpoint plus the resolved facts needed to build a snapshot."""
    id: str
    name: str
    protocol: str
    container: str
    container_port: int
    host_port: int


@dataclass
class TailnetRoute:
    """Private ingress adaptation and identity policy for one Serve port."""
    id: str
    endpoint_id: str
    path_prefix: str
    allowed_users: tuple[str, ...] = ()


def _pack_any(msg: betterproto2.Message, type_url: str) -> PbAny:
    return PbAny(type_url=type_url, value=bytes(msg))


def _socket_address(host: str, port: int) -> Address:
    return Address(
        socket_address=SocketAddress(
            protocol=SocketAddressProtocol.TCP,
            address=host,
            port_value=port,
        )
    )


def _ads_config() -> ConfigSource:
    return ConfigSource(
        resource_api_version=ApiVersion.V3,
        ads=AggregatedConfigSource(),
    )


def _http_listener(name: str, host: str, port: int, routes_name: str) -> Listener:
    router = Router()
    hcm = HttpConnectionManager(
        codec_type=HttpConnectionManagerCodecType.AUTO,
        stat_prefix=name,
        request_timeout=timedelta(seconds=0),
        stream_idle_timeout=timedelta(seconds=600),
        request_headers_timeout=timedelta(seconds=300),
        use_remote_address=True,
        skip_xff_append=False,
        rds=Rds(
            config_source=_ads_config(),
            route_config_name=routes_name,
        ),
        http_filters=[
            HttpFilter(
                name="envoy.filters.http.router",
                typed_config=_pack_any(
                    router,
                    "type.googleapis.com/envoy.extensions.filters.http.router.v3.Router",
                ),
            ),
        ],
    )
    return Listener(
        name=name,
        address=_socket_address(host, port),
        filter_chains=[
            FilterChain(
                filters=[
                    Filter(
                        name="envoy.filters.network.http_connection_manager",
                        typed_config=_pack_any(
                            hcm,
                            "type.googleapis.com/envoy.extensions.filters.network."
                            "http_connection_manager.v3.HttpConnectionManager",
                        ),
                    ),
                ],
            ),
        ],
    )


def make_http_listener() -> Listener:
    return _http_listener(
        HTTP_LISTENER_NAME,
        "0.0.0.0",
        HTTP_LISTEN_PORT,
        HTTP_ROUTES_NAME,
    )


def make_tailnet_listener() -> Listener:
    return _http_listener(
        TAILNET_LISTENER_NAME,
        "127.0.0.1",
        TAILNET_LISTEN_PORT,
        TAILNET_ROUTES_NAME,
    )


def make_tcp_listener(ep: ResolvedEndpoint) -> Listener:
    cluster_name = _cluster_name(ep.id)
    proxy = TcpProxy(
        stat_prefix=f"tcp_{ep.id}",
        cluster=cluster_name,
    )
    return Listener(
        name=f"tcp_listener_{ep.id}",
        address=_socket_address("0.0.0.0", ep.host_port),
        filter_chains=[
            FilterChain(
                filters=[
                    Filter(
                        name="envoy.filters.network.tcp_proxy",
                        typed_config=_pack_any(
                            proxy,
                            "type.googleapis.com/envoy.extensions.filters."
                            "network.tcp_proxy.v3.TcpProxy",
                        ),
                    ),
                ],
            ),
        ],
    )


def _cluster_name(endpoint_id: str) -> str:
    return f"cluster_{endpoint_id.replace('/', '_')}"


def make_cluster(ep: ResolvedEndpoint) -> Cluster:
    name = _cluster_name(ep.id)
    return Cluster(
        name=name,
        connect_timeout=timedelta(seconds=5),
        type=ClusterDiscoveryType.STRICT_DNS,
        lb_policy=ClusterLbPolicy.ROUND_ROBIN,
        load_assignment=ClusterLoadAssignment(
            cluster_name=name,
            endpoints=[
                LocalityLbEndpoints(
                    lb_endpoints=[
                        LbEndpoint(
                            endpoint=EpEndpoint(
                                address=_socket_address(ep.container, ep.container_port),
                            ),
                        ),
                    ],
                ),
            ],
        ),
    )


def make_route_config(http_endpoints: List[ResolvedEndpoint]) -> RouteConfiguration:
    vhosts: List[VirtualHost] = []
    if not http_endpoints:
        vhosts.append(VirtualHost(
            name="default_404",
            domains=["*"],
            routes=[Route(
                match=RouteMatch(prefix="/"),
                route=RouteAction(cluster="__unused__"),
            )],
        ))
    for endpoint in http_endpoints:
        vhosts.append(VirtualHost(
            name=endpoint.name,
            domains=[endpoint.name],
            request_headers_to_remove=TAILSCALE_IDENTITY_HEADERS,
            routes=[Route(
                match=RouteMatch(prefix="/"),
                route=RouteAction(
                    cluster=_cluster_name(endpoint.id),
                    timeout=timedelta(seconds=0),
                    idle_timeout=timedelta(seconds=300),
                ),
            )],
        ))
    return RouteConfiguration(name=HTTP_ROUTES_NAME, virtual_hosts=vhosts)


def make_tailnet_route_config(
    routes: List[TailnetRoute],
    endpoints: List[ResolvedEndpoint],
) -> RouteConfiguration:
    endpoints_by_id = {endpoint.id: endpoint for endpoint in endpoints}
    missing = [
        route.endpoint_id for route in routes
        if route.endpoint_id not in endpoints_by_id
    ]
    if missing:
        raise ValueError(f"tailnet routes reference missing endpoints: {missing}")
    ordered = sorted(routes, key=lambda route: len(route.path_prefix), reverse=True)
    envoy_routes: List[Route] = []
    for route in ordered:
        for user_login in route.allowed_users:
            envoy_routes.append(Route(
                name=f"{route.id}:{user_login}",
                match=RouteMatch(
                    prefix=route.path_prefix,
                    headers=[HeaderMatcher(
                        name="tailscale-user-login",
                        string_match=StringMatcher(
                            exact=user_login,
                            ignore_case=True,
                        ),
                    )],
                ),
                route=RouteAction(
                    cluster=_cluster_name(route.endpoint_id),
                    prefix_rewrite="/",
                    host_rewrite_literal=endpoints_by_id[route.endpoint_id].name,
                    timeout=timedelta(seconds=0),
                    idle_timeout=timedelta(seconds=300),
                ),
            ))
        envoy_routes.append(Route(
            name=f"{route.id}:forbidden",
            match=RouteMatch(prefix=route.path_prefix),
            direct_response=DirectResponseAction(
                status=403,
                body=DataSource(inline_string="Forbidden\n"),
            ),
        ))
    return RouteConfiguration(
        name=TAILNET_ROUTES_NAME,
        virtual_hosts=[VirtualHost(
            name="tailnet_private",
            domains=["*"],
            routes=envoy_routes,
        )],
    )


@dataclass
class Snapshot:
    version: str
    listeners: List[Listener]
    routes: List[RouteConfiguration]
    clusters: List[Cluster]


def build_snapshot(
    version: str,
    endpoints: List[ResolvedEndpoint],
    tailnet_routes: List[TailnetRoute] | None = None,
) -> Snapshot:
    http = [endpoint for endpoint in endpoints if endpoint.protocol == "http"]
    tcp = [endpoint for endpoint in endpoints if endpoint.protocol == "tcp"]
    private_routes = tailnet_routes or []

    listeners: List[Listener] = [make_http_listener()]
    listeners.extend(make_tcp_listener(endpoint) for endpoint in tcp)
    routes = [make_route_config(http)]
    if private_routes:
        listeners.append(make_tailnet_listener())
        routes.append(make_tailnet_route_config(private_routes, http))

    return Snapshot(
        version=version,
        listeners=listeners,
        routes=routes,
        clusters=[make_cluster(endpoint) for endpoint in (http + tcp)],
    )
