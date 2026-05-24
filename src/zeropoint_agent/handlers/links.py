"""Var linking endpoint — wire a Var to another Var, or break the link.

A `Var` can be in one of two modes:
  - **Literal**: `value` is set; resolve emits it.
  - **Linked**:  `value` is None and the Var has another Var as a parent.
                 Resolve walks the parent's VarResult and emits its value.

Linking is the single mutation that handles both:
  - **Link**:   PUT /api/links/{id}  body {"target": "<target_id>"}
                Sets value=None, drops any existing link edge, adds the
                new edge in one transaction.
  - **Unlink**: PUT /api/links/{id}  body {"target": null}
                Drops the existing link edge. Leaves value at None — the
                Var is now an empty literal slot the user must fill.

Validates:
  - source and target both exist
  - source has 'w' in effective perms
  - source is a Var (or subclass)
  - target is a Var (or subclass)
  - source != target
  - linking does not create a cycle (target not a descendant of source)

The transaction is wrapped in graph_transaction so any failure rolls
back both the node-state change and the edge change.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from zeropoint_agent.graph_transaction import graph_transaction
from zeropoint_agent.handlers.mutations import _require_bit
from zeropoint_agent.inode import NodeStatus
from zeropoint_agent.nodes.config.var import Var
from zeropoint_agent.query import _get_all_descendants

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/links", tags=["links"])


class LinkRequest(BaseModel):
    target: Optional[str] = None  # null = unlink


def _find_existing_var_parent(entry, dag) -> Optional[str]:
    """Return the id of the source's current Var parent, if any.

    A Var in linked mode has exactly one non-Namespace Var parent;
    we identify it by isinstance check at resolution time.
    """
    for pid in entry.parents:
        try:
            parent = dag.get(pid)
        except KeyError:
            continue
        if isinstance(parent.node, Var):
            return pid
    return None


@router.put("/{node_id:path}")
async def link_var(node_id: str, body: LinkRequest, request: Request) -> Dict[str, Any]:
    """Wire a Var to another Var, or break the existing link.

    See module docstring for full semantics.
    """
    node_id = unquote(node_id)
    dag = request.app.state.dag

    try:
        entry = dag.get(node_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Node not found: {node_id}")

    if not isinstance(entry.node, Var):
        raise HTTPException(
            status_code=400,
            detail=f"Node {node_id!r} is not a Var; can't link.",
        )

    _require_bit(dag, node_id, "w", "writable")

    target_id = body.target

    if target_id is not None:
        # ---- link / re-link ----
        if target_id == node_id:
            raise HTTPException(
                status_code=400,
                detail="Cannot link a Var to itself.",
            )
        try:
            target_entry = dag.get(target_id)
        except KeyError:
            raise HTTPException(
                status_code=404,
                detail=f"Link target not found: {target_id}",
            )
        if not isinstance(target_entry.node, Var):
            raise HTTPException(
                status_code=400,
                detail=f"Link target {target_id!r} is not a Var.",
            )
        # Cycle check: target must not be a descendant of source.
        descendants = _get_all_descendants(dag, node_id)
        if target_id in descendants:
            raise HTTPException(
                status_code=400,
                detail=(f"Cannot link to {target_id!r}: it is a descendant of "
                        f"{node_id!r}; would create a cycle."),
            )

    # ---- apply (transactional) -----------------------------------------
    try:
        with graph_transaction(dag):
            old_link = _find_existing_var_parent(entry, dag)

            # Always clear value when changing the link state.
            entry.node.value = None
            if dag._store:
                from zeropoint_agent.handlers.mutations import _node_config_dict
                dag._store.set_config(node_id, _node_config_dict(entry.node))

            # Drop the existing var parent (if any) before adding the new one.
            if old_link is not None and old_link != target_id:
                entry.parents.remove(old_link)
                if dag._store:
                    dag._store.remove_edge(old_link, node_id)

            # Add the new link, if any.
            if target_id is not None and target_id != old_link:
                if target_id not in entry.parents:
                    entry.parents.append(target_id)
                if dag._store:
                    dag._store.add_edge(target_id, node_id)

            # Invalidate descendants — the value changed.
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
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("link_var failed for %s", node_id)
        raise HTTPException(status_code=500, detail=str(e)) from e

    return {
        "ok": True,
        "node_id": node_id,
        "target": target_id,
        "previous_target": old_link,
    }
