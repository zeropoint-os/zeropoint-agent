"""Mutation endpoints — update and delete nodes."""

import logging
from typing import Dict, Any
from urllib.parse import unquote

from fastapi import APIRouter, Request, HTTPException

from zeropoint_agent.inode import NodeStatus
from zeropoint_agent.query import query_dag, _get_all_descendants

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dag", tags=["mutations"])


@router.put("/{node_id}")
async def update_node(node_id: str, config: Dict[str, Any], request: Request):
    """Update a node's desired state (config).

    Changes the node's config, resets it to PENDING, and
    invalidates all descendants.
    """
    dag = request.app.state.dag
    try:
        entry = dag.get(node_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Node not found: {node_id}")

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

    Removes in reverse topo order (children first), calling
    remove() on each node.
    """
    pattern = unquote(pattern)
    dag = request.app.state.dag
    matched_ids = query_dag(dag, pattern)

    if not matched_ids:
        raise HTTPException(status_code=404, detail=f"No nodes match: {pattern}")

    removed = []
    for nid in reversed(matched_ids):
        try:
            entry = dag.get(nid)
            entry.node.remove()
            dag.remove(nid)
            removed.append(nid)
        except Exception as e:
            logger.error(f"Failed to remove {nid}: {e}")

    return {"ok": True, "removed": removed, "count": len(removed)}
