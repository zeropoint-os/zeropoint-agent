"""Envoy container lifecycle — runs `envoyproxy/envoy` and points it at our xDS.

Mirrors internal/envoy/manager.go from the Go agent. The agent owns a
single Envoy container named `zeropoint-envoy`, connected to the
`zeropoint-network` bridge so it can resolve `<module>-main` DNS for
upstream clusters. The bootstrap config is regenerated on every start
so a moving xDS gateway IP doesn't stick.

In mock mode we don't touch Docker.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CONTAINER_NAME = "zeropoint-envoy"
NETWORK_NAME = "zeropoint-network"
DEFAULT_IMAGE = "envoyproxy/envoy:v1.31-latest"
ADMIN_PORT = 9901

BOOTSTRAP_TEMPLATE = """node:
  id: zeropoint-node
  cluster: zeropoint-cluster

dynamic_resources:
  ads_config:
    api_type: GRPC
    transport_api_version: V3
    grpc_services:
      - envoy_grpc:
          cluster_name: xds_cluster
  cds_config:
    resource_api_version: V3
    ads: {{}}
  lds_config:
    resource_api_version: V3
    ads: {{}}

static_resources:
  clusters:
    - name: xds_cluster
      type: STATIC
      connect_timeout: 1s
      typed_extension_protocol_options:
        envoy.extensions.upstreams.http.v3.HttpProtocolOptions:
          "@type": type.googleapis.com/envoy.extensions.upstreams.http.v3.HttpProtocolOptions
          explicit_http_config:
            http2_protocol_options: {{}}
      load_assignment:
        cluster_name: xds_cluster
        endpoints:
          - lb_endpoints:
              - endpoint:
                  address:
                    socket_address:
                      address: {xds_host}
                      port_value: {xds_port}

admin:
  address:
    socket_address:
      address: 0.0.0.0
      port_value: {admin_port}
"""


def _storage_root() -> Path:
    return Path(os.environ.get("ZEROPOINT_ROOT_PATH", ".")).expanduser().resolve()


def write_bootstrap(xds_host: str, xds_port: int) -> Path:
    """Generate (or overwrite) the bootstrap.yaml. Returns the absolute path."""
    envoy_dir = _storage_root() / "envoy"
    envoy_dir.mkdir(parents=True, exist_ok=True)
    path = envoy_dir / "bootstrap.yaml"
    path.write_text(BOOTSTRAP_TEMPLATE.format(
        xds_host=xds_host, xds_port=xds_port, admin_port=ADMIN_PORT,
    ))
    return path


def _docker_client():
    """Return a docker client, or None if docker isn't available."""
    try:
        import docker  # type: ignore
        return docker.from_env()
    except Exception as e:
        logger.warning("docker client unavailable: %s", e)
        return None


def _ensure_network(client) -> Optional[str]:
    """Ensure zeropoint-network exists; return its gateway IP."""
    try:
        nets = client.networks.list(names=[NETWORK_NAME])
        if nets:
            net = nets[0]
        else:
            net = client.networks.create(NETWORK_NAME, driver="bridge")
            logger.info("created docker network %s", NETWORK_NAME)
        info = net.attrs or {}
        ipam = (info.get("IPAM") or {}).get("Config") or []
        for cfg in ipam:
            gw = cfg.get("Gateway")
            if gw:
                return gw
    except Exception as e:
        logger.warning("failed to ensure network: %s", e)
    return None


def _ensure_image(client, image: str) -> None:
    try:
        client.images.get(image)
    except Exception:
        logger.info("pulling envoy image %s", image)
        try:
            client.images.pull(image)
        except Exception as e:
            logger.warning("failed to pull envoy image: %s", e)


def ensure_envoy(
    xds_port: int = 18000,
    http_port: int = 80,
    https_port: int = 443,
    image: str = DEFAULT_IMAGE,
) -> Optional[dict]:
    """Ensure the zeropoint-envoy container is running and points at our xDS.

    Returns a dict describing the container state (state, image, id) or
    None if docker is unavailable. Idempotent — safe to call repeatedly.
    """
    client = _docker_client()
    if client is None:
        return None

    gateway = _ensure_network(client) or "host.docker.internal"
    bootstrap = write_bootstrap(gateway, xds_port)
    _ensure_image(client, image)

    try:
        c = client.containers.get(CONTAINER_NAME)
    except Exception:
        c = None

    if c is not None:
        if c.status == "running":
            logger.info("envoy already running (id=%s)", c.short_id)
            return {"state": "running", "image": image, "id": c.short_id}
        try:
            c.start()
            logger.info("started existing envoy container (id=%s)", c.short_id)
            return {"state": "running", "image": image, "id": c.short_id}
        except Exception as e:
            logger.warning("failed to start existing envoy: %s; recreating", e)
            try:
                c.remove(force=True)
            except Exception:
                pass

    try:
        c = client.containers.run(
            image=image,
            name=CONTAINER_NAME,
            command=["-c", "/etc/envoy/bootstrap.yaml", "--service-node",
                     "zeropoint-node", "--service-cluster", "zeropoint-cluster"],
            detach=True,
            volumes={str(bootstrap): {"bind": "/etc/envoy/bootstrap.yaml", "mode": "ro"}},
            ports={f"{http_port}/tcp": http_port,
                   f"{https_port}/tcp": https_port,
                   f"{ADMIN_PORT}/tcp": ADMIN_PORT},
            restart_policy={"Name": "unless-stopped"},
            network=NETWORK_NAME,
        )
        logger.info("created envoy container (id=%s)", c.short_id)
        return {"state": "running", "image": image, "id": c.short_id}
    except Exception as e:
        logger.error("failed to start envoy container: %s", e)
        return {"state": "error", "image": image, "error": str(e)}


def envoy_status() -> Optional[dict]:
    """Return current state of the envoy container (or None)."""
    client = _docker_client()
    if client is None:
        return None
    try:
        c = client.containers.get(CONTAINER_NAME)
        return {
            "state": c.status,
            "image": c.image.tags[0] if c.image and c.image.tags else "",
            "id": c.short_id,
        }
    except Exception:
        return {"state": "not_running"}


def stop_envoy() -> None:
    client = _docker_client()
    if client is None:
        return
    try:
        c = client.containers.get(CONTAINER_NAME)
        c.stop(timeout=5)
        logger.info("stopped envoy container")
    except Exception as e:
        logger.debug("stop envoy: %s", e)
