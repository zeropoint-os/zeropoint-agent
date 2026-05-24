"""Resolve endpoints — run the graph."""

import logging
from dataclasses import asdict
from urllib.parse import unquote

from fastapi import APIRouter, Request, HTTPException

from zeropoint_agent.inode import ResolveMode, NodeStatus
from zeropoint_agent.query import query_dag
from zeropoint_agent.module_ports import sync_module_ports
from zeropoint_agent.handlers import ResolveRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dag", tags=["resolve"])

MODE_MAP = {
    "live": ResolveMode.LIVE,
    "dry_run": ResolveMode.DRY_RUN,
    "mock": ResolveMode.MOCK,
}


def _parse_mode(mode_str: str) -> ResolveMode:
    mode = MODE_MAP.get(mode_str)
    if not mode:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid mode: {mode_str}. Use: live, dry_run, mock"
        )
    return mode


def _effective_mode(requested: str, request: Request) -> ResolveMode:
    """Resolve the effective mode, respecting the global default.

    If ZEROPOINT_MODE=mock, live requests are blocked.
    If no mode specified in request, use the global default.
    """
    default = getattr(request.app.state, "default_mode", "mock")

    # Use global default if request doesn't specify
    mode_str = requested or default

    # Block live mode when global is mock (safety)
    if mode_str == "live" and default == "mock":
        raise HTTPException(
            status_code=403,
            detail="Live mode blocked: server is running in mock mode "
                   "(set ZEROPOINT_MODE=live to enable)"
        )

    return _parse_mode(mode_str)


def _results_to_response(dag, results, mode_str, pattern=None):
    nodes = []
    for nid, status in results.items():
        entry = dag.get(nid)
        node_data = {
            "id": nid,
            "type": type(entry.node).__name__,
            "status": status.value,
            "error": entry.error,
        }
        if entry.output and hasattr(entry.output, "__dataclass_fields__"):
            node_data["output"] = asdict(entry.output)
        nodes.append(node_data)

    summary = {}
    for s in results.values():
        summary[s.value] = summary.get(s.value, 0) + 1

    resp = {
        "ok": all(s in (NodeStatus.SUCCESS, NodeStatus.PENDING_REBOOT)
                  for s in results.values()),
        "mode": mode_str,
        "summary": summary,
        "nodes": nodes,
    }
    if pattern:
        resp["pattern"] = pattern
    return resp


@router.post("/resolve")
async def resolve_graph(body: ResolveRequest, request: Request):
    """Resolve the entire graph.

    Mode defaults to ZEROPOINT_MODE env var. Live mode blocked in mock mode.
    """
    try:
        mode = _effective_mode(body.mode, request)
        dag = request.app.state.dag
        results = dag.resolve(mode=mode)
        try:
            n = sync_module_ports(dag)
            if n:
                logger.info("synced %d port nodes after resolve", n)
        except Exception as e:
            logger.warning("port sync failed (continuing): %s", e)
        return _results_to_response(dag, results, mode.value)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to resolve graph: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/resolve/{pattern:path}")
async def resolve_subgraph(pattern: str, body: ResolveRequest, request: Request):
    """Resolve only the matched subgraph."""
    pattern = unquote(pattern)
    try:
        mode = _effective_mode(body.mode, request)
        dag = request.app.state.dag
        matched_ids = query_dag(dag, pattern)

        if not matched_ids:
            raise HTTPException(status_code=404, detail=f"No nodes match: {pattern}")

        results = dag.resolve_subset(matched_ids, mode=mode)
        try:
            n = sync_module_ports(dag)
            if n:
                logger.info("synced %d port nodes after resolve", n)
        except Exception as e:
            logger.warning("port sync failed (continuing): %s", e)
        return _results_to_response(dag, results, mode.value, pattern)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to resolve subgraph: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
