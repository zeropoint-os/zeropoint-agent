"""Expose / Unexpose actions — wire a module's port to a LAN-visible Endpoint.

Endpoint nodes are not user-creatable from the type picker. The only
way one comes into existence is via `POST /api/expose {port_var_id}`.

Expose:
  1. Validates the target is a port-typed Var (Var[int]).
  2. Locates the owning module namespace (the Var's Namespace parent).
  3. Picks a default `name` from the module leaf + port suffix.
  4. Picks `protocol` from the sibling `*_protocol` var if present;
     defaults to http for 80/443, tcp otherwise.
  5. Allocates a host_port (tcp only) in 10000-60000.
  6. Creates `modules/<owning>/endpoint_<name>` with parents=[owning_ns,
     port_var_id] inside a graph_transaction.

Unexpose:
  Deletes any endpoint whose parents include port_var_id.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Set

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from zeropoint_agent.graph_transaction import graph_transaction
from zeropoint_agent.inode import NodeStatus
from zeropoint_agent.nodes.config.namespace import Namespace
from zeropoint_agent.nodes.config.var import Var
from zeropoint_agent.nodes.user.endpoint import Endpoint
from zeropoint_agent.xds.reconciler import reconcile as xds_reconcile

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["expose"])

TCP_PORT_MIN = 10000
TCP_PORT_MAX = 60000


class ExposeRequest(BaseModel):
    port_var_id: str
    name: Optional[str] = None
    protocol: Optional[str] = None


class UnexposeRequest(BaseModel):
    port_var_id: str


def _owning_namespace_id(entry, dag) -> Optional[str]:
    """Find the Namespace parent id of a node."""
    for pid in entry.parents:
        p = dag.nodes.get(pid)
        if p is not None and isinstance(p.node, Namespace):
            return pid
    return None


def _sibling_protocol(port_var_id: str, dag) -> Optional[str]:
    """If port_var_id is named port_<n>, look for port_<n>_protocol sibling."""
    if "/" not in port_var_id:
        return None
    parent_path, leaf = port_var_id.rsplit("/", 1)
    if not leaf.startswith("port_"):
        return None
    proto_id = f"{parent_path}/{leaf}_protocol"
    p = dag.nodes.get(proto_id)
    if p is None:
        return None
    val = getattr(p.output, "value", None)
    if val is None and isinstance(p.output, dict):
        val = p.output.get("value")
    if isinstance(val, str) and val:
        return val
    fallback = getattr(p.node, "value", None)
    if isinstance(fallback, str) and fallback:
        return fallback
    return None


def _existing_endpoint_names(ns_id: str, dag) -> Set[str]:
    out: Set[str] = set()
    prefix = f"{ns_id}/endpoint_"
    for nid, entry in dag.nodes.items():
        if nid.startswith(prefix) and isinstance(entry.node, Endpoint):
            out.add(entry.node.name)
    return out


def _allocate_host_port(dag) -> int:
    used: Set[int] = set()
    for entry in dag.nodes.values():
        if not isinstance(entry.node, Endpoint):
            continue
        if entry.node.protocol != "tcp":
            continue
        if entry.node.host_port:
            used.add(int(entry.node.host_port))
    for p in range(TCP_PORT_MIN, TCP_PORT_MAX + 1):
        if p not in used:
            return p
    raise HTTPException(status_code=500, detail="No free TCP host ports left")


def _pick_default_protocol(port_var_id: str, dag) -> str:
    sibling = _sibling_protocol(port_var_id, dag)
    if sibling in ("http", "tcp"):
        return sibling
    # Heuristic: well-known http ports default to http; everything else tcp.
    port_entry = dag.nodes.get(port_var_id)
    if port_entry is not None:
        val = getattr(port_entry.output, "value", None)
        if val is None and isinstance(port_entry.output, dict):
            val = port_entry.output.get("value")
        try:
            if int(val) in (80, 443, 8080, 8443):
                return "http"
        except (TypeError, ValueError):
            pass
    return "tcp"


def _pick_default_name(ns_id: str, port_leaf: str, dag) -> str:
    """Choose a non-colliding default name.

    Start with the module leaf (the namespace's leaf segment). If that
    collides with an existing endpoint on the same namespace, append
    the port name.
    """
    module_leaf = ns_id.rsplit("/", 1)[-1]
    used = _existing_endpoint_names(ns_id, dag)
    if module_leaf not in used:
        return module_leaf
    # port_<x> -> <x>
    port_part = port_leaf[len("port_"):] if port_leaf.startswith("port_") else port_leaf
    candidate = f"{module_leaf}_{port_part}"
    if candidate not in used:
        return candidate
    # Otherwise number-suffix until unique.
    i = 2
    while True:
        c = f"{candidate}_{i}"
        if c not in used:
            return c
        i += 1


@router.post("/expose")
async def expose(body: ExposeRequest, request: Request) -> Dict[str, Any]:
    dag = request.app.state.dag
    port_var_id = body.port_var_id

    port_entry = dag.nodes.get(port_var_id)
    if port_entry is None:
        raise HTTPException(status_code=404, detail=f"Port var not found: {port_var_id}")
    if not isinstance(port_entry.node, Var):
        raise HTTPException(
            status_code=400,
            detail=f"{port_var_id!r} is not a Var; can't expose it.")

    owning_ns = _owning_namespace_id(port_entry, dag)
    if owning_ns is None:
        raise HTTPException(
            status_code=400,
            detail=f"Port var {port_var_id!r} has no namespace parent — can't derive owning module.")

    port_leaf = port_var_id.rsplit("/", 1)[-1]
    name = body.name or _pick_default_name(owning_ns, port_leaf, dag)
    if not name.replace("_", "").replace("-", "").isalnum():
        raise HTTPException(
            status_code=400,
            detail=f"Endpoint name {name!r} must be alphanumeric (with _ or -).")

    protocol = body.protocol or _pick_default_protocol(port_var_id, dag)
    if protocol not in ("http", "tcp"):
        raise HTTPException(
            status_code=400,
            detail=f"protocol must be 'http' or 'tcp', got {protocol!r}")

    endpoint_id = f"{owning_ns}/endpoint_{name}"
    if endpoint_id in dag.nodes:
        raise HTTPException(
            status_code=409,
            detail=f"Endpoint {endpoint_id!r} already exists.")

    host_port = 0
    if protocol == "tcp":
        host_port = _allocate_host_port(dag)

    try:
        with graph_transaction(dag):
            dag.add(
                endpoint_id,
                Endpoint(name=name, protocol=protocol, host_port=host_port),
                parents=[owning_ns, port_var_id],
                perms="rwd",
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("expose failed for %s", port_var_id)
        raise HTTPException(status_code=500, detail=str(e)) from e

    runner = getattr(request.app.state, "xds", None)
    if runner is not None:
        try:
            await xds_reconcile(dag, runner)
        except Exception as e:
            logger.warning("xDS reconcile after expose failed: %s", e)

    return {
        "ok": True,
        "endpoint_id": endpoint_id,
        "name": name,
        "protocol": protocol,
        "host_port": host_port,
        "port_var_id": port_var_id,
    }


@router.post("/unexpose")
async def unexpose(body: UnexposeRequest, request: Request) -> Dict[str, Any]:
    dag = request.app.state.dag
    port_var_id = body.port_var_id

    to_delete = [
        nid for nid, entry in dag.nodes.items()
        if isinstance(entry.node, Endpoint) and port_var_id in entry.parents
    ]
    if not to_delete:
        return {"ok": True, "deleted": []}

    try:
        with graph_transaction(dag):
            for nid in to_delete:
                dag.remove(nid)
    except Exception as e:
        logger.exception("unexpose failed for %s", port_var_id)
        raise HTTPException(status_code=500, detail=str(e)) from e

    runner = getattr(request.app.state, "xds", None)
    if runner is not None:
        try:
            await xds_reconcile(dag, runner)
        except Exception as e:
            logger.warning("xDS reconcile after unexpose failed: %s", e)

    return {"ok": True, "deleted": to_delete}
