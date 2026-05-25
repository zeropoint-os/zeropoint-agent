"""DockerNetwork — ensures a named Docker bridge network exists.

Has SystemDocker as a parent (so it only runs after the daemon is
reachable). In LIVE mode, creates the network if missing. In MOCK,
emits success without touching docker.

Anything that needs a particular bridge — Envoy needing
`zeropoint-network`, modules' containers attaching to it for cross-
container DNS — depends transitively on the corresponding
DockerNetwork node. There's no more lazy "create if missing"
scattered around the codebase; this node IS the invariant.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, ClassVar

from zeropoint_agent.inode import INode, ResolveMode, NodeResult, readonly

logger = logging.getLogger(__name__)


@dataclass
class DockerNetworkResult:
    name: str = ""
    id: str = ""
    gateway: str = ""
    driver: str = "bridge"


@dataclass
class DockerNetwork(INode[Any, DockerNetworkResult]):
    """Ensures a Docker bridge network with the given name exists."""

    default_perms: ClassVar[str] = "r--"

    name: str = readonly(default="")
    driver: str = readonly(default="bridge")

    def resolve(self, input: Any, mode: ResolveMode) -> NodeResult[DockerNetworkResult]:
        if not self.name:
            return NodeResult.failed("DockerNetwork.name is required")

        if mode == ResolveMode.MOCK:
            return NodeResult.success(DockerNetworkResult(
                name=self.name, id="mock", gateway="172.18.0.1",
                driver=self.driver,
            ))

        try:
            import docker  # type: ignore
            client = docker.from_env(timeout=5)
            nets = client.networks.list(names=[self.name])
            if nets:
                net = nets[0]
            else:
                net = client.networks.create(self.name, driver=self.driver)
                logger.info("created docker network %s", self.name)
            info = net.attrs or {}
            gateway = ""
            for cfg in (info.get("IPAM") or {}).get("Config") or []:
                gw = cfg.get("Gateway")
                if gw:
                    gateway = gw
                    break
            return NodeResult.success(DockerNetworkResult(
                name=self.name, id=net.id, gateway=gateway,
                driver=self.driver,
            ))
        except Exception as e:
            return NodeResult.failed(f"failed to ensure network {self.name}: {e}")

    def verify(self, mode: ResolveMode) -> NodeResult[DockerNetworkResult]:
        return self.resolve(None, mode)

    def remove(self, mode: ResolveMode) -> NodeResult[DockerNetworkResult]:
        if mode == ResolveMode.MOCK:
            return NodeResult.success()
        try:
            import docker  # type: ignore
            client = docker.from_env(timeout=5)
            for net in client.networks.list(names=[self.name]):
                try:
                    net.remove()
                    logger.info("removed docker network %s", self.name)
                except Exception as e:
                    logger.warning("failed to remove network %s: %s", self.name, e)
        except Exception as e:
            logger.debug("network remove probe failed: %s", e)
        return NodeResult.success()
