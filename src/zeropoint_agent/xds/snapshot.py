"""Build Envoy xDS snapshots from a list of resolved Endpoints.

Mirrors internal/xds/snapshot.go from the Go agent. Each call returns
a fresh (listeners, routes, clusters) triple at a monotonic version.

HTTP endpoints share one Listener on :80 with HCM/RDS; each gets a
VirtualHost (domain = name and name.local) routed to its own Cluster.
TCP endpoints get one Listener per endpoint bound to host_port, with a
TcpProxy filter pointing at its Cluster. Clusters are STRICT_DNS with
a single endpoint at `<container>-main:<container_port>`.
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
    Address, AggregatedConfigSource, ApiVersion, ConfigSource,
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
    Route, RouteAction, RouteConfiguration, RouteMatch, VirtualHost,
)
from envoy_data_plane.envoy.extensions.filters.http.router.v3 import Router
from envoy_data_plane.envoy.extensions.filters.network.http_connection_manager.v3 import (
    HttpConnectionManager, HttpConnectionManagerCodecType,
    HttpFilter, Rds,
)
from envoy_data_plane.envoy.extensions.filters.network.tcp_proxy.v3 import TcpProxy
from envoy_data_plane.google.protobuf import Any as PbAny

HTTP_LISTENER_NAME = "http_listener"
HTTP_ROUTES_NAME = "http_routes"
HTTP_LISTEN_PORT = 80


@dataclass
class ResolvedEndpoint:
    """An Endpoint plus the resolved facts needed to build a snapshot."""
    id: str                # graph id, stable resource discriminator
    name: str              # mDNS hostname / vhost name (http) or label (tcp)
    protocol: str          # "http" | "tcp"
    container: str         # docker DNS name on zeropoint-network (e.g. "echo-main")
    container_port: int    # port inside the container
    host_port: int         # only used for tcp


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
    """ADS-pointing ConfigSource used by RDS inside the HTTP listener."""
    return ConfigSource(
        resource_api_version=ApiVersion.V3,
        ads=AggregatedConfigSource(),
    )


def make_http_listener() -> Listener:
    router = Router()
    hcm = HttpConnectionManager(
        codec_type=HttpConnectionManagerCodecType.AUTO,
        stat_prefix="http",
        request_timeout=timedelta(seconds=0),
        stream_idle_timeout=timedelta(seconds=600),
        request_headers_timeout=timedelta(seconds=300),
        use_remote_address=True,
        skip_xff_append=False,
        rds=Rds(
            config_source=_ads_config(),
            route_config_name=HTTP_ROUTES_NAME,
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
        name=HTTP_LISTENER_NAME,
        address=_socket_address("0.0.0.0", HTTP_LISTEN_PORT),
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


def make_tcp_listener(ep: ResolvedEndpoint) -> Listener:
    cluster_name = _cluster_name(ep)
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


def _cluster_name(ep: ResolvedEndpoint) -> str:
    return f"cluster_{ep.id.replace('/', '_')}"


def make_cluster(ep: ResolvedEndpoint) -> Cluster:
    name = _cluster_name(ep)
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
    for ep in http_endpoints:
        domains = [ep.name]
        if not ep.name.endswith(".local"):
            domains.append(f"{ep.name}.local")
        vhosts.append(VirtualHost(
            name=ep.name,
            domains=domains,
            routes=[Route(
                match=RouteMatch(prefix="/"),
                route=RouteAction(
                    cluster=_cluster_name(ep),
                    timeout=timedelta(seconds=0),
                    idle_timeout=timedelta(seconds=300),
                ),
            )],
        ))
    return RouteConfiguration(name=HTTP_ROUTES_NAME, virtual_hosts=vhosts)


@dataclass
class Snapshot:
    """A self-contained xDS snapshot keyed by monotonic version."""
    version: str
    listeners: List[Listener]
    routes: List[RouteConfiguration]
    clusters: List[Cluster]


def build_snapshot(version: str, endpoints: List[ResolvedEndpoint]) -> Snapshot:
    http = [e for e in endpoints if e.protocol == "http"]
    tcp = [e for e in endpoints if e.protocol == "tcp"]

    listeners: List[Listener] = [make_http_listener()]
    for ep in tcp:
        listeners.append(make_tcp_listener(ep))

    routes = [make_route_config(http)]
    clusters = [make_cluster(e) for e in (http + tcp)]

    return Snapshot(
        version=version,
        listeners=listeners,
        routes=routes,
        clusters=clusters,
    )
