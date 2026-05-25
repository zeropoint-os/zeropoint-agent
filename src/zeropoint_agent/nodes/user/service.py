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
        result = ServiceResult(
            name=self.name, port=port, protocol=protocol,
            transport=transport, description=desc, value=port,
        )
        # If a user has expressed intent to expose me (an Exposure child
        # exists), publish my slice to the xDS cache. If not, ensure
        # I'm absent from it. Either way, I own this — no central
        # reconciler.
        self._sync_xds(result, mode)
        return NodeResult.success(result)

    def _sync_xds(self, my_result: "ServiceResult", mode: ResolveMode) -> None:
        """Write or clear my xDS slice based on whether I have an Exposure child.

        In LIVE mode, also ensure the target module's container is on
        zeropoint-network so Envoy's STRICT_DNS cluster can resolve it.
        """
        if self.dag is None or not self.id:
            return
        cache = _xds_cache_for(self.dag)
        if cache is None:
            return

        # Find an Exposure child (the user's "expose this" declaration).
        from zeropoint_agent.nodes.user.exposure import Exposure
        exposure = None
        for nid, entry in self.dag.nodes.items():
            if isinstance(entry.node, Exposure) and self.id in entry.parents:
                exposure = entry.node
                break

        if exposure is None:
            cache.clear(self.id)
            return

        # Owning module = first Namespace ancestor under modules/.
        module_leaf = _owning_module_leaf(self.dag, self.id)
        if not module_leaf:
            cache.clear(self.id)
            return
        container = f"{module_leaf}-main"

        # Lazy import to avoid pulling xds at module load time.
        from zeropoint_agent.xds.snapshot import ResolvedEndpoint
        slice_ = ResolvedEndpoint(
            id=self.id,
            name=exposure.name or module_leaf,
            protocol=my_result.protocol,
            container=container,
            container_port=my_result.port,
            host_port=int(exposure.host_port or 0),
        )
        cache.set(self.id, slice_)

        if mode == ResolveMode.LIVE:
            try:
                from zeropoint_agent.envoy_manager import (
                    ensure_container_on_zeropoint_network,
                )
                ensure_container_on_zeropoint_network(container)
            except Exception as e:
                logger.debug("network attach for %s failed: %s", container, e)

    def verify(self, mode: ResolveMode) -> NodeResult[ServiceResult]:
        return NodeResult.pending_reboot(ServiceResult(
            name=self.name, port=0, protocol="tcp", value=0,
        ))

    def remove(self, mode: ResolveMode) -> NodeResult[ServiceResult]:
        # If I'm being removed, drop my xDS slice too.
        if self.dag is not None and self.id:
            cache = _xds_cache_for(self.dag)
            if cache is not None:
                cache.clear(self.id)
        return NodeResult.success()


def _xds_cache_for(dag):
    """Pull the xDS cache out of the DAG's runtime context, or None."""
    return getattr(dag, "xds_cache", None)


def _owning_module_leaf(dag, node_id: str) -> str:
    """Walk ancestors to the first Namespace under modules/; return its leaf."""
    from zeropoint_agent.nodes.config.namespace import Namespace
    visited = set()
    queue = [node_id]
    while queue:
        nid = queue.pop()
        if nid in visited:
            continue
        visited.add(nid)
        entry = dag.nodes.get(nid)
        if entry is None:
            continue
        for pid in entry.parents:
            p = dag.nodes.get(pid)
            if p is None:
                continue
            if isinstance(p.node, Namespace) and pid.startswith("modules/"):
                return pid.rsplit("/", 1)[-1]
            queue.append(pid)
    return ""
