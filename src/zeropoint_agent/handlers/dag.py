"""DAG build and inspection endpoints."""

import os
import logging
from pathlib import Path
from dataclasses import asdict

from fastapi import APIRouter, Request, HTTPException

from zeropoint_agent.dag import DAG
from zeropoint_agent.graph_store import GraphStore
from zeropoint_agent.handlers import NodeSpec, GraphBuildRequest, NODE_REGISTRY, node_to_dict

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


@router.post("/build")
async def build_graph(request_body: GraphBuildRequest, request: Request):
    """Build a complete graph from a list of node specs.

    Nodes must be in topological order (parents before children).
    Type checking happens at each edge.
    """
    try:
        store_path = os.environ.get("ZEROPOINT_ROOT_PATH", ".")
        db_path = str(Path(store_path) / "data" / "graph.db")

        store = GraphStore(db_path)
        store.clear()
        dag = DAG(store=store)

        for spec in request_body.nodes:
            node = _create_node(spec)
            dag.add(spec.id, node, parents=spec.parents)

        request.app.state.store = store
        request.app.state.dag = dag

        return {
            "ok": True,
            "nodes": len(request_body.nodes),
            "message": f"Graph built with {len(request_body.nodes)} nodes"
        }
    except (TypeError, KeyError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to build graph: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/nodes")
async def add_node(spec: NodeSpec, request: Request):
    """Add a single node to the existing graph.

    If a NamespaceNode parent is specified, that parent must have `w`
    in its effective permissions (creating a child counts as writing
    to the namespace).
    """
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
        node = _create_node(spec)
        dag.add(spec.id, node, parents=spec.parents)
        return {"ok": True, "node_id": spec.id}
    except HTTPException:
        raise
    except (TypeError, KeyError) as e:
        raise HTTPException(status_code=400, detail=str(e))


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
