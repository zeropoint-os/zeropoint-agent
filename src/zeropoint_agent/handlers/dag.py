"""DAG inspection + per-node mutations."""

import logging
import re
from dataclasses import asdict

from fastapi import APIRouter, Request, HTTPException

from zeropoint_agent.handlers import NodeSpec, NODE_REGISTRY, node_to_dict

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dag", tags=["dag"])

# Each path segment of a node id must be a valid identifier. Slashes
# are the path separator and are stripped before checking. Keeping the
# alphabet narrow (A-Z, a-z, 0-9, _) avoids URL-encoding surprises and
# keeps the graph addressable from the CLI without quoting.
_NODE_ID_SEGMENT = re.compile(r"^[A-Za-z0-9_]+$")


def _validate_node_id(node_id: str) -> None:
    """Raise HTTPException 400 if any path segment of node_id is not a valid identifier."""
    if not node_id:
        raise HTTPException(status_code=400, detail="node id must not be empty")
    for seg in node_id.split("/"):
        if not _NODE_ID_SEGMENT.match(seg):
            raise HTTPException(
                status_code=400,
                detail=(f"invalid node id {node_id!r}: segment {seg!r} must "
                        f"match [A-Za-z0-9_]+"),
            )


def _create_node(spec: NodeSpec):
    """Instantiate an INode from a NodeSpec."""
    if spec.type not in NODE_REGISTRY:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown node type: {spec.type}. "
                   f"Available: {list(NODE_REGISTRY.keys())}"
        )
    cls = NODE_REGISTRY[spec.type]
    try:
        return cls(**spec.config)
    except TypeError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid config for {spec.type}: {e}"
        )


@router.post("/nodes")
async def add_node(spec: NodeSpec, request: Request):
    """Add a single node to the existing graph.

    Returns 409 if a node with the given id already exists.
    Returns 403 if a Namespace parent isn't writable.

    Wrapped in graph_transaction so a failure halfway through (e.g.
    type-check rejection after the perms check passes) doesn't leave
    a partial node lingering in the store.
    """
    from zeropoint_agent.dag import NodeExists
    from zeropoint_agent.graph_transaction import graph_transaction
    try:
        _validate_node_id(spec.id)
        dag = request.app.state.dag
        # Check w on every Namespace parent before mutating.
        from zeropoint_agent.nodes.config.namespace import Namespace
        for pid in spec.parents:
            try:
                parent_entry = dag.get(pid)
            except KeyError:
                continue
            if isinstance(parent_entry.node, Namespace):
                eff = dag.effective_perms(pid)
                if "w" not in eff:
                    raise HTTPException(
                        status_code=403,
                        detail=(f"parent namespace {pid!r} is not writable "
                                f"(cannot add children; effective perms: {eff})")
                    )
        with graph_transaction(dag):
            node = _create_node(spec)
            dag.add(spec.id, node, parents=spec.parents,
                    perms=getattr(spec, "perms", "***") or "***",
                    tags=getattr(spec, "tags", None) or None)
        return {"ok": True, "node_id": spec.id}
    except HTTPException:
        raise
    except NodeExists as e:
        raise HTTPException(status_code=409, detail=str(e))
    except (TypeError, KeyError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/nodes/{node_id:path}")
async def get_node(node_id: str, request: Request):
    """Return a single node by id."""
    from urllib.parse import unquote
    node_id = unquote(node_id)
    dag = request.app.state.dag
    try:
        entry = dag.get(node_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"node not found: {node_id}")
    return node_to_dict(node_id, entry, dag)


@router.get("")
async def get_dag(request: Request):
    """Get the current graph structure with all nodes and edges."""
    dag = request.app.state.dag
    nodes = []
    edges = []

    for nid, entry in dag.nodes.items():
        nodes.append(node_to_dict(nid, entry, dag))
        for parent_id in entry.parents:
            edges.append({"source": parent_id, "target": nid})

    return {"ok": True, "nodes": nodes, "edges": edges}
