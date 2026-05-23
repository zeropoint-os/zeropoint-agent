"""Mutation endpoints — update and delete nodes."""

import logging
from typing import Dict, Any
from urllib.parse import unquote

from fastapi import APIRouter, Request, HTTPException

from zeropoint_agent.inode import NodeStatus, ResolveMode
from zeropoint_agent.graph_transaction import graph_transaction
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


def _node_config_dict(node) -> Dict[str, Any]:
    """Snapshot of a node's serializable config (matches what's persisted).

    Same filter as DAG.add() and PUT use when writing to the store —
    public instance attributes only. Used to give `on_config_changed`
    a clean before/after picture without leaking internal state.
    """
    return {k: v for k, v in node.__dict__.items() if not k.startswith("_")}


def _resolve_mode(request: Request) -> ResolveMode:
    """Pull the agent's configured default resolve mode, defaulting to LIVE."""
    state = request.app.state
    if not hasattr(state, "default_mode"):
        return ResolveMode.LIVE
    return {
        "live": ResolveMode.LIVE,
        "dry_run": ResolveMode.DRY_RUN,
        "mock": ResolveMode.MOCK,
    }.get(state.default_mode, ResolveMode.LIVE)


@router.put("/{node_id:path}")
async def update_node(node_id: str, body: Dict[str, Any], request: Request):
    """Update a node's desired state and/or permissions.

    Body shape: `{config?: {...}, perms?: "rwd"}` — both optional but
    at least one required. Requires `w` in the node's effective
    permissions.

    Sequence (transactional, all-or-nothing):
      1. Validate body shape, perms format, field names.
      2. Take a snapshot of graph.db.
      3. Call the node's `on_config_changed(old, new)` hook — this
         is where side effects like DirectoryVar's directory move
         happen. If the hook raises, we roll back and return the
         error.
      4. Apply the config + perms changes in memory.
      5. Persist to the store; invalidate descendants.
      6. On any exception in 3-5, the snapshot guard restores
         graph.db and reloads the in-memory DAG.
    """
    from zeropoint_agent.dag import _valid_perms
    node_id = unquote(node_id)
    dag = request.app.state.dag
    try:
        entry = dag.get(node_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Node not found: {node_id}")

    _require_bit(dag, node_id, "w", "writable")

    config = body.get("config")
    perms = body.get("perms")
    if config is None and perms is None:
        raise HTTPException(
            status_code=400,
            detail="PUT body must include at least one of: config, perms",
        )

    # ---- validation -----------------------------------------------------
    if config is not None:
        if not isinstance(config, dict):
            raise HTTPException(status_code=400, detail="config must be an object")
        for key in config:
            if not hasattr(entry.node, key):
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown config field '{key}' for "
                           f"{type(entry.node).__name__}",
                )

    if perms is not None:
        if not isinstance(perms, str) or not _valid_perms(perms):
            raise HTTPException(
                status_code=400,
                detail=f"perms must be a 3-char string from r/w/d/-/*; "
                       f"got {perms!r}",
            )

    # ---- transactional apply -------------------------------------------
    invalidated = 0
    mode = _resolve_mode(request)
    try:
        with graph_transaction(dag):
            if config is not None:
                old_config = _node_config_dict(entry.node)
                # Hook may raise — that aborts the whole transaction.
                merged_config = {**old_config, **config}
                try:
                    entry.node.on_config_changed(old_config, merged_config, mode)
                except Exception as e:
                    raise HTTPException(status_code=400, detail=str(e)) from e

                for key, value in config.items():
                    setattr(entry.node, key, value)

                entry.status = NodeStatus.PENDING
                entry.output = None
                entry.error = None
                for desc_id in _get_all_descendants(dag, node_id):
                    desc = dag.get(desc_id)
                    desc.status = NodeStatus.PENDING
                    desc.output = None
                    desc.error = None

                if dag._store:
                    new_config = _node_config_dict(entry.node)
                    dag._store.set_config(node_id, new_config)
                    dag._store.update_status(node_id, "pending")
                    dag._store.invalidate_descendants(node_id)
                invalidated = len(_get_all_descendants(dag, node_id))

            if perms is not None:
                entry.perms = perms
                if dag._store:
                    dag._store.set_perms(node_id, perms)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("update_node failed for %s", node_id)
        raise HTTPException(status_code=500, detail=str(e)) from e

    return {
        "ok": True,
        "node_id": node_id,
        "invalidated": invalidated,
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

    mode = _resolve_mode(request)

    removed = []
    with graph_transaction(dag):
        for nid in reversed(matched_ids):
            try:
                entry = dag.get(nid)
                entry.node.remove(mode)
                dag.remove(nid)
                removed.append(nid)
            except Exception as e:
                logger.error(f"Failed to remove {nid}: {e}")

    return {"ok": True, "removed": removed, "count": len(removed)}
