"""Endpoint — declares that a module's port is reachable on the LAN.

An Endpoint says "the LAN can reach me at <name>, and I route to that
port". Its data parent is a port `OutputVar[int]` (typically synced
under `modules/<m>/port_<n>` from a module's `main_ports` terraform
output). The Endpoint reads its parent's value at resolve time; the
xDS reconciler then renders a snapshot wiring `<owning-module>-main:<port>`
to the right Envoy listener/route/cluster.

Endpoints aren't user-created free-form: the `/api/expose` action
creates them with the right parent already in place. They're also
hidden from the free-form type picker. Users CAN still edit `name` and
`protocol`, and CAN change the data parent via the existing Var picker
to retarget which port the endpoint forwards.

`host_port` is only relevant for tcp endpoints — it's the LAN-visible
port the Envoy TCP listener binds to. The expose action allocates one
in 10000-60000 at creation; http endpoints all share Envoy's :80.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, ClassVar, Optional

from zeropoint_agent.inode import INode, NodeResult, ResolveMode, readonly
from zeropoint_agent.nodes.config.var import VarResult
from zeropoint_agent.nodes.config.namespace import NamespaceResult

logger = logging.getLogger(__name__)


@dataclass
class EndpointResult:
    """Contract for an Endpoint.

    `target_port` is the resolved port int (read from the data parent).
    `target_container` is derived at reconciliation time from the data
    parent's owning module — kept off this contract so the Endpoint
    itself stays decoupled from the rest of the graph.
    """
    name: str
    protocol: str
    host_port: int
    target_port: Optional[int] = None


@dataclass
class Endpoint(INode[Any, EndpointResult]):
    """A LAN-visible endpoint.

    Carries `name`, `protocol`, and (for tcp) `host_port`. The actual
    target port comes from a `Var[int]` parent — usually a port
    OutputVar synced from terraform's `main_ports` output.
    """

    # Endpoint exists structurally (under a module namespace) and reads
    # its target from a Var parent. It can be safely deleted; "w" is
    # meaningful (rename / protocol toggle).
    default_perms: ClassVar[str] = "rwd"

    name: str = ""
    protocol: str = "http"
    host_port: int = readonly(default=0)

    def _read_port(self, input_val: Any) -> Optional[int]:
        """Pull an int value off the first VarResult in `input_val`."""
        def _as_int(v: Any) -> Optional[int]:
            if v is None:
                return None
            try:
                return int(v)
            except (TypeError, ValueError):
                return None

        if isinstance(input_val, VarResult):
            return _as_int(input_val.value)
        if isinstance(input_val, dict):
            for v in input_val.values():
                if isinstance(v, VarResult):
                    got = _as_int(v.value)
                    if got is not None:
                        return got
        return None

    def resolve(self, input: Any, mode: ResolveMode) -> NodeResult[EndpointResult]:
        if mode == ResolveMode.MOCK:
            return NodeResult.success(EndpointResult(
                name=self.name, protocol=self.protocol,
                host_port=self.host_port, target_port=0))

        port = self._read_port(input)
        result = EndpointResult(
            name=self.name, protocol=self.protocol,
            host_port=self.host_port, target_port=port)
        if port is None:
            return NodeResult.failed(
                f"Endpoint {self.name!r} has no port target — link it to "
                f"a port Var via the picker", output=result)
        return NodeResult.success(result)

    def verify(self, mode: ResolveMode) -> NodeResult[EndpointResult]:
        return NodeResult.pending_reboot(EndpointResult(
            name=self.name, protocol=self.protocol,
            host_port=self.host_port))

    def remove(self, mode: ResolveMode) -> NodeResult[EndpointResult]:
        return NodeResult.success()
