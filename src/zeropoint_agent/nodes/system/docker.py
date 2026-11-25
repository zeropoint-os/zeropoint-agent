"""SystemDocker — verifies the Docker daemon is reachable from the agent.

Read-only observation node. Resolves SUCCESS when the docker SDK can
talk to the daemon, ERROR otherwise. Doesn't try to *start* dockerd —
that's docker-init's job (the devcontainer feature handles it).

Anything that needs docker (DockerNetwork, SystemEnvoy, Service's
network attach) should have system/docker as a transitive ancestor
so its own resolve runs only after this one succeeds.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, ClassVar, Optional

from zeropoint_agent.inode import INode, ResolveMode, NodeResult

logger = logging.getLogger(__name__)


@dataclass
class SystemDockerResult:
    running: bool = False
    version: str = ""
    api_version: str = ""


@dataclass
class SystemDocker(INode[Any, SystemDockerResult]):
    """Read-only healthcheck for the docker daemon."""

    default_perms: ClassVar[str] = "r--"

    def _probe(self) -> SystemDockerResult:
        try:
            import docker  # type: ignore
            client = docker.from_env(timeout=5)
            info = client.version()
            return SystemDockerResult(
                running=True,
                version=info.get("Version", ""),
                api_version=info.get("ApiVersion", ""),
            )
        except Exception as e:
            logger.debug("docker probe failed: %s", e)
            return SystemDockerResult(running=False)

    def resolve(self, input: Any, mode: ResolveMode) -> NodeResult[SystemDockerResult]:
        if mode == ResolveMode.MOCK:
            return NodeResult.success(SystemDockerResult(
                running=True, version="mock", api_version="mock"))
        result = self._probe()
        if result.running:
            return NodeResult.success(result)
        return NodeResult.failed("docker daemon not reachable", result)

    def verify(self, mode: ResolveMode) -> NodeResult[SystemDockerResult]:
        return self.resolve(None, mode)

    def remove(self, mode: ResolveMode) -> NodeResult[SystemDockerResult]:
        return NodeResult.success()
