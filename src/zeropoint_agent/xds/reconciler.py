"""Endpoint reconciler — walks the graph and pushes xDS snapshots.

Given a DAG and the running xDS runner, walks every Endpoint node,
resolves its target port (from its non-namespace Var parent) and
derives the container DNS name from that parent's owning module
namespace.

Cross-module wiring is supported: the container name comes from the
*target port's* module, not the endpoint's parent module.

Endpoints whose target is missing, unlinked, or unresolved are
skipped — their `last_error` is logged but they don't fail the
overall reconciliation.
"""

from __future__ import annotations

import logging
from typing import List, Optional, TYPE_CHECKING

from zeropoint_agent.nodes.config.namespace import Namespace
from zeropoint_agent.nodes.config.var import Var
from zeropoint_agent.nodes.user.endpoint import Endpoint
from zeropoint_agent.xds.snapshot import ResolvedEndpoint, build_snapshot

if TYPE_CHECKING:
    from zeropoint_agent.dag import DAG
    from zeropoint_agent.xds.runner import XdsRunner

logger = logging.getLogger(__name__)


def _read_var_int(entry) -> Optional[int]:
    """Read a Var node's resolved int value."""
    out = entry.output
    if out is None:
        # Fall back to the node's literal field.
        val = getattr(entry.node, "value", None)
        try:
            return int(val) if val is not None else None
        except (TypeError, ValueError):
            return None
    val = getattr(out, "value", None)
    if val is None and isinstance(out, dict):
        val = out.get("value")
    try:
        return int(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def _read_var_str(entry) -> Optional[str]:
    out = entry.output
    if out is None:
        val = getattr(entry.node, "value", None)
        return str(val) if val is not None else None
    val = getattr(out, "value", None)
    if val is None and isinstance(out, dict):
        val = out.get("value")
    return str(val) if val is not None else None


def _owning_namespace_id(entry, dag: "DAG") -> Optional[str]:
    for pid in entry.parents:
        p = dag.nodes.get(pid)
        if p is not None and isinstance(p.node, Namespace):
            return pid
    return None


def _data_var_parent_id(entry, dag: "DAG") -> Optional[str]:
    """Endpoint's non-namespace Var parent — the target port var."""
    for pid in entry.parents:
        p = dag.nodes.get(pid)
        if p is None:
            continue
        if isinstance(p.node, Namespace):
            continue
        if isinstance(p.node, Var):
            return pid
    return None


def collect_resolved_endpoints(dag: "DAG") -> List[ResolvedEndpoint]:
    """Walk the DAG and produce a ResolvedEndpoint per healthy Endpoint."""
    out: List[ResolvedEndpoint] = []
    for nid, entry in dag.nodes.items():
        if not isinstance(entry.node, Endpoint):
            continue

        target_id = _data_var_parent_id(entry, dag)
        if target_id is None:
            logger.warning("endpoint %s has no Var parent — skipping", nid)
            continue
        target_entry = dag.nodes.get(target_id)
        if target_entry is None:
            logger.warning("endpoint %s target %s missing — skipping", nid, target_id)
            continue

        port = _read_var_int(target_entry)
        if port is None or port <= 0:
            logger.info("endpoint %s target %s has no resolved port — skipping",
                        nid, target_id)
            continue

        target_ns = _owning_namespace_id(target_entry, dag)
        if target_ns is None:
            logger.warning("endpoint %s target %s has no module namespace — skipping",
                           nid, target_id)
            continue
        # Container DNS name = <target-module-leaf>-main, matching the
        # terraform convention (every module names its primary container
        # ${zp_module_id}-main).
        module_leaf = target_ns.rsplit("/", 1)[-1]
        container = f"{module_leaf}-main"

        out.append(ResolvedEndpoint(
            id=nid,
            name=entry.node.name,
            protocol=entry.node.protocol,
            container=container,
            container_port=port,
            host_port=int(entry.node.host_port or 0),
        ))
    return out


async def reconcile(dag: "DAG", runner: "XdsRunner") -> int:
    """Build a snapshot from the current graph and push it to the xDS server.

    Also reconciles mDNS: every http endpoint gets `<name>.local`
    advertised on the LAN; removed endpoints get unregistered.

    Returns the number of endpoints in the new snapshot.
    """
    endpoints = collect_resolved_endpoints(dag)
    version = runner.ads.next_version()
    snapshot = build_snapshot(version, endpoints)
    await runner.ads.update_snapshot(snapshot)

    try:
        from zeropoint_agent.mdns import get_registry
        registry = get_registry()
        registry.reconcile([
            (ep.name, 80) for ep in endpoints if ep.protocol == "http"
        ])
    except Exception as e:
        logger.warning("mDNS reconcile failed (continuing): %s", e)

    return len(endpoints)
