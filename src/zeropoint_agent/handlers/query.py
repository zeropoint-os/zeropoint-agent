"""Glob-based graph query and health endpoints."""

import logging
from urllib.parse import unquote

from fastapi import APIRouter, Request

from zeropoint_agent.query import query_dag, _get_all_descendants
from zeropoint_agent.handlers import node_to_dict

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dag", tags=["query"])


@router.get("/query/{pattern:path}")
async def query_graph(pattern: str, request: Request,
                      status: str = None, type: str = None):
    """Query the graph with a glob-style path pattern.

    Examples:
        /api/dag/query/**                  → all nodes
        /api/dag/query/disk-sda            → single node
        /api/dag/query/disk-*              → all disks
        /api/dag/query/disk-sda/*          → direct children
        /api/dag/query/disk-sda/**         → entire subtree
        /api/dag/query/**/ollama           → find anywhere
        /api/dag/query/**?status=error     → all errors
        /api/dag/query/**?type=ModuleNode  → all modules
    """
    pattern = unquote(pattern)
    dag = request.app.state.dag
    matched_ids = query_dag(dag, pattern, status=status, node_type=type)
    nodes = [node_to_dict(nid, dag.get(nid)) for nid in matched_ids]

    summary = {}
    for n in nodes:
        s = n["status"]
        summary[s] = summary.get(s, 0) + 1

    return {"ok": True, "pattern": pattern, "count": len(nodes),
            "summary": summary, "nodes": nodes}


@router.get("/health/{pattern:path}")
async def graph_health(pattern: str, request: Request):
    """Health check via glob pattern.

    Examples:
        /api/dag/health/**              → whole graph health
        /api/dag/health/disk-sda/**     → disk subtree health
        /api/dag/health/ollama          → single node health
    """
    pattern = unquote(pattern)
    dag = request.app.state.dag
    matched_ids = query_dag(dag, pattern)

    nodes = []
    for nid in matched_ids:
        entry = dag.get(nid)
        nodes.append({
            "id": nid,
            "type": type(entry.node).__name__,
            "status": entry.status.value,
            "error": entry.error,
        })

    total = len(nodes)
    success = sum(1 for n in nodes if n["status"] == "success")
    healthy = total > 0 and success == total
    needs_reboot = any(n["status"] == "pending_reboot" for n in nodes)
    errors = [n for n in nodes if n["status"] == "error"]

    return {
        "healthy": healthy,
        "needs_reboot": needs_reboot,
        "total": total,
        "success": success,
        "errors": len(errors),
        "summary": {s: sum(1 for n in nodes if n["status"] == s)
                    for s in set(n["status"] for n in nodes)} if nodes else {},
        "nodes": nodes,
    }


@router.get("/health")
async def graph_health_all(request: Request):
    """Health of the entire graph."""
    return await graph_health("**", request)
