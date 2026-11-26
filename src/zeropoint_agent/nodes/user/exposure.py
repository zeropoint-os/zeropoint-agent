"""Application routes into the agent-managed machine ingress.

An Exposure persists the canonical application slug in `name`. HTTP exposure
renders as `http://<slug>-<machine>.local/` through the single `system/envoy`
listener, while the application continues serving at `/`.

Every Exposure is parented to both its Service and `system/envoy`.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, ClassVar

from zeropoint_agent.inode import INode, NodeResult, ResolveMode, readonly

logger = logging.getLogger(__name__)

SYSTEM_ENVOY_NODE_ID = "system/envoy"
_DNS_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,62})$")


@dataclass
class ExposureResult:
    name: str
    protocol: str = ""
    host_port: int = 0


@dataclass
class Exposure(INode[Any, ExposureResult]):
    """A LAN-visible application route declaration beneath a Service."""

    default_perms: ClassVar[str] = "r--"

    name: str = ""
    host_port: int = readonly(default=0)

    def resolve(self, input: Any, mode: ResolveMode) -> NodeResult[ExposureResult]:
        protocol = ""
        if isinstance(input, dict):
            for value in input.values():
                protocol = _maybe_protocol(value)
                if protocol:
                    break
        else:
            protocol = _maybe_protocol(input) or ""
        return NodeResult.success(ExposureResult(
            name=self.name,
            protocol=protocol,
            host_port=self.host_port,
        ))

    def verify(self, mode: ResolveMode) -> NodeResult[ExposureResult]:
        return NodeResult.pending_reboot(ExposureResult(
            name=self.name,
            host_port=self.host_port,
        ))

    def remove(self, mode: ResolveMode) -> NodeResult[ExposureResult]:
        return NodeResult.success()


def canonical_local_hostname(slug: str, machine_name: str) -> str:
    """Render a resolvable single-label hostname for one application."""
    normalized_slug = slug.strip().rstrip(".")
    normalized_machine = machine_name.strip().rstrip(".")
    if normalized_machine.lower().endswith(".local"):
        normalized_machine = normalized_machine[:-6].rstrip(".")
    if not normalized_slug or not normalized_machine:
        raise ValueError("exposure slug and machine identity must be non-empty")
    label = f"{normalized_slug}-{normalized_machine}"
    if not _DNS_LABEL_RE.fullmatch(label):
        raise ValueError(
            "machine-qualified exposure hostname must be a DNS label of at "
            "most 63 letters, digits, or hyphens"
        )
    return f"{label}.local"


def _maybe_protocol(holder: Any) -> str:
    """Best-effort read of a `protocol` attribute from a parent's output."""
    protocol = getattr(holder, "protocol", None)
    if protocol is None and isinstance(holder, dict):
        protocol = holder.get("protocol")
    return str(protocol) if protocol else ""
