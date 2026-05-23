"""DAG inspection + per-node mutations."""

import logging
from dataclasses import asdict

from fastapi import APIRouter, Request, HTTPException

from zeropoint_agent.handlers import NodeSpec, NODE_REGISTRY, node_to_dict

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dag", tags=["dag"])


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
    Returns 403 if a NamespaceNode parent isn't writable.

    Wrapped in graph_transaction so a failure halfway through (e.g.
    type-check rejection after the perms check passes) doesn't leave
    a partial node lingering in the store.
    """
    from zeropoint_agent.dag import NodeExists
    from zeropoint_agent.graph_transaction import graph_transaction
    try:
        dag = request.app.state.dag
        # Check w on every NamespaceNode parent before mutating.
        from zeropoint_agent.nodes.config.namespace import NamespaceNode
        for pid in spec.parents:
            try:
                parent_entry = dag.get(pid)
            except KeyError:
                continue
            if isinstance(parent_entry.node, NamespaceNode):
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
                    perms=getattr(spec, "perms", "***") or "***")
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
