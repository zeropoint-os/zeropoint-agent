"""SystemEnvoy — read-only diagnostic node surfacing the Envoy container state.

Not user-creatable from the picker. The agent bootstraps a single
`system/envoy` node on startup (when not in mock mode) and updates its
output on each Envoy lifecycle event. All fields are readonly so the
user can see what's running but not modify anything via the inspector.

Lifecycle commands (start/stop/restart) live on the runner, not on
this node — the node is purely a window into state.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, ClassVar, Optional

from zeropoint_agent.inode import INode, NodeResult, ResolveMode, readonly

logger = logging.getLogger(__name__)


@dataclass
class SystemEnvoyResult:
    state: str = "unknown"
    image: str = ""
    container_id: str = ""
    xds_port: int = 0
    http_port: int = 80
    https_port: int = 443


@dataclass
class SystemEnvoy(INode[Any, SystemEnvoyResult]):
    """Read-only diagnostic for the agent-managed Envoy container."""

    default_perms: ClassVar[str] = "r--"

    xds_port: int = readonly(default=18000)
    http_port: int = readonly(default=80)
    https_port: int = readonly(default=443)

    def resolve(self, input: Any, mode: ResolveMode) -> NodeResult[SystemEnvoyResult]:
        if mode == ResolveMode.MOCK:
            return NodeResult.success(SystemEnvoyResult(
                state="mock",
                image="envoyproxy/envoy:mock",
                xds_port=self.xds_port,
                http_port=self.http_port,
                https_port=self.https_port,
            ))
        from zeropoint_agent.envoy_manager import envoy_status
        info = envoy_status() or {}
        return NodeResult.success(SystemEnvoyResult(
            state=info.get("state", "unknown"),
            image=info.get("image", ""),
            container_id=info.get("id", ""),
            xds_port=self.xds_port,
            http_port=self.http_port,
            https_port=self.https_port,
        ))

    def verify(self, mode: ResolveMode) -> NodeResult[SystemEnvoyResult]:
        return self.resolve(None, mode)

    def remove(self, mode: ResolveMode) -> NodeResult[SystemEnvoyResult]:
        return NodeResult.success()
