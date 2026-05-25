"""Exposure — declares that a Service is reachable on the LAN.

An Exposure is purely structural. Its presence as a child of a
Service IS the user's "expose this" declaration. No data parent,
no link target — the Service it's parented to already knows its
own port, protocol, container, etc.

Exposure carries display config:
  - `name`     — mDNS hostname (http) or label (tcp). Defaults at
                 creation time to the owning module's leaf name.
  - `host_port` — auto-allocated for tcp exposures; unused for http
                  (which all share Envoy's :80 listener).

When a Service resolves and finds an Exposure child, it writes a
slice to the xDS cache. When the Exposure is removed, the Service's
next resolve sees no child and writes nothing — the slice naturally
disappears on cache.commit().
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, ClassVar

from zeropoint_agent.inode import INode, NodeResult, ResolveMode, readonly

logger = logging.getLogger(__name__)


@dataclass
class ExposureResult:
    name: str
    protocol: str = ""
    host_port: int = 0


@dataclass
class Exposure(INode[Any, ExposureResult]):
    """A LAN-visible exposure declaration. Lives as a child of a Service.

    Created and destroyed only via /api/expose and /api/unexpose. The
    class default is r-- — users cannot edit or delete an Exposure
    from the inspector. The expose/unexpose handlers go through
    dag.add()/dag.remove() directly, bypassing the perms check.
    """

    default_perms: ClassVar[str] = "r--"

    name: str = ""
    host_port: int = readonly(default=0)

    def resolve(self, input: Any, mode: ResolveMode) -> NodeResult[ExposureResult]:
        # Exposure has no work of its own. Its presence as a child of
        # a Service is observed by the Service.resolve. We just emit
        # our config for the inspector.
        protocol = ""
        if isinstance(input, dict):
            for v in input.values():
                proto = _maybe_protocol(v)
                if proto:
                    protocol = proto
                    break
        else:
            protocol = _maybe_protocol(input) or ""
        return NodeResult.success(ExposureResult(
            name=self.name, protocol=protocol, host_port=self.host_port,
        ))

    def verify(self, mode: ResolveMode) -> NodeResult[ExposureResult]:
        return NodeResult.pending_reboot(ExposureResult(
            name=self.name, host_port=self.host_port,
        ))

    def remove(self, mode: ResolveMode) -> NodeResult[ExposureResult]:
        return NodeResult.success()


def _maybe_protocol(holder: Any) -> str:
    """Best-effort read of a `protocol` attribute from a parent's output."""
    p = getattr(holder, "protocol", None)
    if p is None and isinstance(holder, dict):
        p = holder.get("protocol")
    return str(p) if p else ""

