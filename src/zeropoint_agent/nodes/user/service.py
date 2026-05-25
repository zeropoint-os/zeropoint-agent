"""Service — a {port, protocol, transport} bundle inside a module output.

Discovery rule: any OutputVar whose resolved value contains a dict
with `port` (int) and `protocol` (string) keys is a bundle. After
each resolve, the agent's discovery pass creates one Service node
per discovered bundle, parented to the owning OutputVar and keyed
by the dict key (e.g. `main_ports.http` → child `http`).

A Service node:
  - exposes `port`, `protocol`, `transport` as its resolved output
  - reads from its parent OutputVar via `key`
  - on resolve, looks for a child `Exposure` (the user's "expose"
    declaration). If found AND mode is LIVE, writes a slice to the
    xDS cache. If no child, nothing is written.

There is no central reconciler. Service.resolve IS the agent's xDS
push for that service. A resolve cycle wraps cache.begin()/commit()
so disappeared services drop out automatically — the graph IS the
source of truth.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, ClassVar, Optional

from zeropoint_agent.inode import INode, NodeResult, ResolveMode, readonly
from zeropoint_agent.nodes.config.namespace import NamespaceResult
from zeropoint_agent.nodes.config.var import VarResult

logger = logging.getLogger(__name__)


@dataclass
class ServiceResult:
    """Contract for a Service node.

    Carries the same shape we want to surface to a Var consumer:
    a single value (the port int), plus full bundle metadata.
    """
    name: str
    port: int
    protocol: str
    transport: str = "tcp"
    description: str = ""
    # VarResult-compatible fields so a Service can be a Var parent
    # in places that expect VarResult.value (.value mirrors .port).
    value: int = 0


@dataclass
class Service(INode[Any, ServiceResult]):
    """A discoverable port+protocol bundle on a module's output.

    Created by the discovery pass after every resolve; not user-creatable.
    Read-only: the underlying data is the module's terraform output.
    """

    # Created by the agent (discovery), never by the user. The class is
    # hidden from the type picker (see node_types._PICKER_HIDDEN).
    default_perms: ClassVar[str] = "r--"

    name: str = ""               # leaf key inside the parent's dict (e.g. "http")
    key: str = readonly(default="")  # dotted key into parent OutputVar's value

    def _read_bundle(self, input_val: Any) -> Optional[dict]:
        """Pull our bundle out of the parent OutputVar's resolved value.

        The parent's output is a VarResult whose `value` is the dict that
        terraform produced (JSON-encoded as a string for OutputVar's
        existing stringification). We parse it back and descend.
        """
        import json

        def _from(holder: Any) -> Optional[dict]:
            val = getattr(holder, "value", None)
            if val is None and isinstance(holder, dict):
                val = holder.get("value")
            if val is None:
                return None
            # OutputVar JSON-encodes dict values when emitting VarResult.
            if isinstance(val, str):
                try:
                    val = json.loads(val)
                except (ValueError, TypeError):
                    return None
            if not isinstance(val, dict):
                return None
            # Descend through dotted key.
            cur: Any = val
            for part in self.key.split("."):
                if not isinstance(cur, dict) or part not in cur:
                    return None
                cur = cur[part]
            return cur if isinstance(cur, dict) else None

        direct = _from(input_val)
        if direct is not None:
            return direct
        if isinstance(input_val, dict):
            for v in input_val.values():
                got = _from(v)
                if got is not None:
                    return got
        return None

    def resolve(self, input: Any, mode: ResolveMode) -> NodeResult[ServiceResult]:
        bundle = self._read_bundle(input)
        if bundle is None:
            if mode == ResolveMode.MOCK:
                # Tests / dev: emit a plausible bundle.
                return NodeResult.success(ServiceResult(
                    name=self.name, port=0, protocol="tcp", transport="tcp",
                    value=0,
                ))
            return NodeResult.failed(
                f"Service {self.name!r} (key={self.key!r}) could not read "
                f"its bundle from parent output")
        try:
            port = int(bundle.get("port"))
        except (TypeError, ValueError):
            return NodeResult.failed(
                f"Service {self.name!r}: bundle has no integer port")
        protocol = str(bundle.get("protocol") or "tcp")
        transport = str(bundle.get("transport") or "tcp")
        desc = str(bundle.get("description") or "")
        return NodeResult.success(ServiceResult(
            name=self.name, port=port, protocol=protocol,
            transport=transport, description=desc, value=port,
        ))

    def verify(self, mode: ResolveMode) -> NodeResult[ServiceResult]:
        return NodeResult.pending_reboot(ServiceResult(
            name=self.name, port=0, protocol="tcp", value=0,
        ))

    def remove(self, mode: ResolveMode) -> NodeResult[ServiceResult]:
        return NodeResult.success()
