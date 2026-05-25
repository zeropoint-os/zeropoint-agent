"""Resolve endpoints — run the graph."""

import logging
from dataclasses import asdict
from urllib.parse import unquote

from fastapi import APIRouter, Request, HTTPException

from zeropoint_agent.inode import ResolveMode, NodeStatus
from zeropoint_agent.query import query_dag
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
    default = getattr(request.app.state, "default_mode", "mock")
    mode_str = requested or default
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


async def _resolve_cycle(request: Request, mode: ResolveMode, pattern: str = None):
    """A full resolve cycle wrapped in an xDS cache begin/commit pair.

    Nodes that want to publish to xDS (Service) write into the cache
    via the DAG's `xds_cache` attribute during their own resolve().
    The cycle wrapper:
      1. cache.begin()   — start staging
      2. dag.resolve()   — every node runs; relevant ones write slices
      3. cache.commit()  — atomic swap, schedule flush to Envoy

    Anything not written this cycle is dropped on commit — graph state
    IS the source of truth.
    """
    dag = request.app.state.dag
    cache = None
    runner = getattr(request.app.state, "xds", None)
    if runner is not None:
        cache = runner.cache
        cache.begin()

    try:
        if pattern is None:
            results = dag.resolve(mode=mode)
        else:
            matched_ids = query_dag(dag, pattern)
            if not matched_ids:
                if cache is not None:
                    cache.abort()
                raise HTTPException(
                    status_code=404, detail=f"No nodes match: {pattern}")
            results = dag.resolve_subset(matched_ids, mode=mode)

        if cache is not None:
            cache.commit()
        return results
    except HTTPException:
        if cache is not None:
            cache.abort()
        raise
    except Exception:
        if cache is not None:
            cache.abort()
        raise


@router.post("/resolve")
async def resolve_graph(body: ResolveRequest, request: Request):
    """Resolve the entire graph."""
    try:
        mode = _effective_mode(body.mode, request)
        dag = request.app.state.dag
        results = await _resolve_cycle(request, mode)
        return _results_to_response(dag, results, mode.value)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to resolve graph: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/resolve/{pattern:path}")
async def resolve_subgraph(pattern: str, body: ResolveRequest, request: Request):
    """Resolve a subset.

    Note: subset resolves don't wrap the xDS cache in begin/commit,
    so they only ADD/UPDATE slices for nodes that resolved (Service
    nodes write directly to live). Services that weren't in the
    matched set keep their previous slices.
    """
    pattern = unquote(pattern)
    try:
        mode = _effective_mode(body.mode, request)
        dag = request.app.state.dag
        matched_ids = query_dag(dag, pattern)
        if not matched_ids:
            raise HTTPException(status_code=404, detail=f"No nodes match: {pattern}")
        results = dag.resolve_subset(matched_ids, mode=mode)
        return _results_to_response(dag, results, mode.value, pattern)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to resolve subgraph: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
