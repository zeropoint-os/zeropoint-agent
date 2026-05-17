"""Mutation endpoints — update and delete nodes."""

import logging
from typing import Dict, Any
from urllib.parse import unquote

from fastapi import APIRouter, Request, HTTPException

from zeropoint_agent.inode import NodeStatus, ResolveMode
from zeropoint_agent.query import query_dag, _get_all_descendants

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dag", tags=["mutations"])


def _require_bit(dag, node_id: str, bit: str, action_label: str) -> None:
    """Raise HTTP 403 if the node's effective perms lack the bit."""
    eff = dag.effective_perms(node_id)
    if bit not in eff:
        raise HTTPException(
            status_code=403,
            detail=(f"node {node_id!r} is not {action_label} "
                    f"(effective perms: {eff})")
        )


@router.put("/{node_id:path}")
async def update_node(node_id: str, config: Dict[str, Any], request: Request):
    """Update a node's desired state (config).

    Requires `w` in the node's effective permissions.

    Changes the node's config, resets it to PENDING, and
    invalidates all descendants.
    """
    from urllib.parse import unquote
    node_id = unquote(node_id)
    dag = request.app.state.dag
    try:
        entry = dag.get(node_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Node not found: {node_id}")

    _require_bit(dag, node_id, "w", "writable")

    for key, value in config.items():
        if hasattr(entry.node, key):
            setattr(entry.node, key, value)
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown config field '{key}' for {type(entry.node).__name__}"
            )

    entry.status = NodeStatus.PENDING
    entry.output = None
    entry.error = None

    for desc_id in _get_all_descendants(dag, node_id):
        desc = dag.get(desc_id)
        desc.status = NodeStatus.PENDING
        desc.output = None
        desc.error = None

    if dag._store:
        dag._store.update_status(node_id, "pending")
        dag._store.invalidate_descendants(node_id)

    return {
        "ok": True,
        "node_id": node_id,
        "invalidated": len(_get_all_descendants(dag, node_id)),
    }


@router.delete("/{pattern:path}")
async def delete_nodes(pattern: str, request: Request):
    """Remove nodes matching the glob pattern.

    Requires `d` in the effective permissions of every matched node.
    Removes in reverse topo order (children first), calling
    remove() on each node.
    """
    pattern = unquote(pattern)
    dag = request.app.state.dag
    matched_ids = query_dag(dag, pattern)

    if not matched_ids:
        raise HTTPException(status_code=404, detail=f"No nodes match: {pattern}")

    # Atomic check: all-or-nothing on permission.
    forbidden = []
    for nid in matched_ids:
        try:
            eff = dag.effective_perms(nid)
            if "d" not in eff:
                forbidden.append((nid, eff))
        except KeyError:
            pass
    if forbidden:
        details = "; ".join(f"{nid} ({eff})" for nid, eff in forbidden)
        raise HTTPException(
            status_code=403,
            detail=f"the following nodes are not deletable: {details}",
        )

    mode = ResolveMode.LIVE
    if hasattr(request.app.state, "default_mode"):
        mode_str = request.app.state.default_mode
        mode = {
            "live": ResolveMode.LIVE,
            "dry_run": ResolveMode.DRY_RUN,
            "mock": ResolveMode.MOCK,
        }.get(mode_str, ResolveMode.LIVE)

    removed = []
    for nid in reversed(matched_ids):
        try:
            entry = dag.get(nid)
            entry.node.remove(mode)
            dag.remove(nid)
            removed.append(nid)
        except Exception as e:
            logger.error(f"Failed to remove {nid}: {e}")

    return {"ok": True, "removed": removed, "count": len(removed)}
