"""Service discovery — find {port, protocol} bundles in OutputVar values.

Canonical rule: any OutputVar whose resolved value is (or contains) a
dict with `port` (int) and `protocol` (string) keys is a Service.

After every resolve, this module:
  1. Walks every `OutputVar` in the graph.
  2. Decodes its resolved value (which is JSON-encoded for dict outputs).
  3. For each `{port, protocol}` bundle found (top-level or one level
     nested), ensures a `Service` child exists keyed by the bundle's
     key path.
  4. Resolves newly-added Services in the same cycle so they don't
     sit PENDING.
  5. For every Service whose resolved status is SUCCESS and which has
     a child Exposure, writes a slice to the xDS cache.

The xDS cache uses begin()/commit() semantics so anything not written
this cycle is automatically absent from the next snapshot.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Iterable, Optional

from zeropoint_agent.inode import NodeStatus, ResolveMode
from zeropoint_agent.nodes.config.namespace import Namespace
from zeropoint_agent.nodes.config.output import OutputVar
from zeropoint_agent.nodes.user.exposure import Exposure
from zeropoint_agent.nodes.user.service import Service
from zeropoint_agent.xds.snapshot import ResolvedEndpoint

if TYPE_CHECKING:
    from zeropoint_agent.dag import DAG
    from zeropoint_agent.xds.cache import XdsCache

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def _is_bundle(d: object) -> bool:
    """Does this dict shape look like a {port, protocol} bundle?"""
    if not isinstance(d, dict):
        return False
    if "port" not in d or "protocol" not in d:
        return False
    try:
        int(d["port"])
    except (TypeError, ValueError):
        return False
    return isinstance(d.get("protocol"), str) and d["protocol"]


def _output_value(entry) -> object:
    """Pull the resolved value off an OutputVar's NodeEntry.

    OutputVar JSON-encodes dict values into VarResult.value. Decode if
    needed so we can introspect bundles.
    """
    out = entry.output
    if out is None:
        return None
    val = getattr(out, "value", None)
    if val is None and isinstance(out, dict):
        val = out.get("value")
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (ValueError, TypeError):
            return val
    return val


def _enumerate_bundles(value: object) -> list[tuple[str, dict]]:
    """Return (key, bundle_dict) pairs from `value`.

    Looks for bundles at the top level (value itself is a bundle, key="")
    and one level nested (value is a dict-of-bundles, key=each inner key).
    """
    out: list[tuple[str, dict]] = []
    if _is_bundle(value):
        out.append(("", value))
        return out
    if isinstance(value, dict):
        for k, v in value.items():
            if _is_bundle(v):
                out.append((str(k), v))
    return out


def _service_id(output_id: str, key: str) -> str:
    """Where a discovered Service node lives.

    For a top-level bundle (key=""), the Service replaces the OutputVar
    in usage, parented to it (id = "<output>/_self"). For nested bundles
    the Service is named by the key (id = "<output>/<key>").
    """
    if not key:
        return f"{output_id}/_self"
    return f"{output_id}/{key}"


def _ensure_service(dag: "DAG", output_id: str, key: str, bundle: dict,
                    new_ids: list[str]) -> str:
    """Idempotently create a Service node under an OutputVar."""
    sid = _service_id(output_id, key)
    if sid in dag.nodes:
        return sid
    leaf = sid.rsplit("/", 1)[-1]
    dag.add(
        sid,
        Service(name=leaf, key=key or ""),
        parents=[output_id],
        perms="r--",
    )
    new_ids.append(sid)
    logger.info("discovered service %s", sid)
    return sid


def _has_exposure_child(dag: "DAG", service_id: str) -> Optional[Exposure]:
    """Return the first Exposure that's a child of `service_id`, or None."""
    for nid, entry in dag.nodes.items():
        if isinstance(entry.node, Exposure) and service_id in entry.parents:
            return entry.node
    return None


def _owning_module_namespace_id(dag: "DAG", node_id: str) -> Optional[str]:
    """Walk ancestry to the first Namespace under modules/."""
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
                return pid
            queue.append(pid)
    return None


def _resolved_endpoint_for(dag: "DAG", service_id: str) -> Optional[ResolvedEndpoint]:
    """Build the ResolvedEndpoint slice for a Service+Exposure pair.

    Returns None if the Service hasn't resolved, has no Exposure child,
    or we can't derive a container DNS name.
    """
    entry = dag.nodes.get(service_id)
    if entry is None or entry.status != NodeStatus.SUCCESS:
        return None
    ep_node = _has_exposure_child(dag, service_id)
    if ep_node is None:
        return None

    out = entry.output
    port = getattr(out, "port", None)
    if port is None and isinstance(out, dict):
        port = out.get("port")
    protocol = getattr(out, "protocol", None)
    if protocol is None and isinstance(out, dict):
        protocol = out.get("protocol")
    if not port or not protocol:
        return None
    try:
        port = int(port)
    except (TypeError, ValueError):
        return None

    module_ns = _owning_module_namespace_id(dag, service_id)
    if module_ns is None:
        return None
    container = f"{module_ns.rsplit('/', 1)[-1]}-main"

    host_port = int(getattr(ep_node, "host_port", 0) or 0)
    return ResolvedEndpoint(
        id=service_id,
        name=ep_node.name or module_ns.rsplit("/", 1)[-1],
        protocol=protocol,
        container=container,
        container_port=port,
        host_port=host_port,
    )


# ---------------------------------------------------------------------------
# Post-resolve sweep
# ---------------------------------------------------------------------------

def discover_and_push(dag: "DAG", cache: "XdsCache", mode: ResolveMode) -> int:
    """The post-resolve sweep.

    - Discovers Service nodes from OutputVar bundles (idempotent).
    - Resolves any newly-added Services so their output is fresh.
    - Walks every Service: if there's a child Exposure and status is
      SUCCESS, writes a slice to `cache` (must be in a begin/commit pair).

    Returns the number of Services that were added or refreshed.
    """
    new_ids: list[str] = []
    for nid, entry in list(dag.nodes.items()):
        if not isinstance(entry.node, OutputVar):
            continue
        value = _output_value(entry)
        for key, bundle in _enumerate_bundles(value):
            _ensure_service(dag, nid, key, bundle, new_ids)

    # Stale-service prune: any Service whose parent OutputVar value no
    # longer contains its bundle is dropped. (graph state is truth.)
    for nid, entry in list(dag.nodes.items()):
        if not isinstance(entry.node, Service):
            continue
        parent_id = entry.parents[0] if entry.parents else None
        parent = dag.nodes.get(parent_id) if parent_id else None
        if parent is None or not isinstance(parent.node, OutputVar):
            continue
        value = _output_value(parent)
        bundles = {k: b for k, b in _enumerate_bundles(value)}
        # Top-level bundle: key=""
        if entry.node.key not in bundles:
            try:
                dag.remove(nid)
                logger.info("pruned stale service %s", nid)
            except Exception as e:
                logger.warning("failed to prune %s: %s", nid, e)

    # Resolve newly-added services so they have output to read.
    if new_ids:
        try:
            dag.resolve_subset(new_ids, mode=mode)
        except Exception as e:
            logger.warning("post-discovery resolve failed: %s", e)

    # Write slices for every healthy Service with an Exposure child.
    # Also re-attach the target containers to zeropoint-network so a
    # graph rehydrate (or DinD restart) re-establishes connectivity
    # without requiring an unexpose/expose cycle.
    pushed = 0
    attach_live = mode == ResolveMode.LIVE
    for nid, entry in dag.nodes.items():
        if not isinstance(entry.node, Service):
            continue
        ep = _resolved_endpoint_for(dag, nid)
        if ep is not None:
            cache.set(nid, ep)
            pushed += 1
            if attach_live:
                try:
                    from zeropoint_agent.envoy_manager import (
                        ensure_container_on_zeropoint_network,
                    )
                    ensure_container_on_zeropoint_network(ep.container)
                except Exception as e:
                    logger.debug("network attach for %s failed: %s",
                                 ep.container, e)
    return pushed
