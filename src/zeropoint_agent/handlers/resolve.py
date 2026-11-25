"""Resolve endpoints backed by persistent GraphRuns and Promises."""

from __future__ import annotations

import logging
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, Request

from zeropoint_agent.handlers import ResolveRequest
from zeropoint_agent.inode import ResolveMode
from zeropoint_agent.query import query_dag

logger = logging.getLogger(__name__)

router = APIRouter(tags=["graph"])

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
            detail=f"Invalid mode: {mode_str}. Use: live, dry_run, mock",
        )
    return mode


def _effective_mode(requested: str | None, request: Request) -> ResolveMode:
    default = getattr(request.app.state, "default_mode", "mock")
    mode_str = requested or default
    if mode_str == "live" and default == "mock":
        raise HTTPException(
            status_code=403,
            detail="Live mode blocked: server is running in mock mode "
                   "(set ZEROPOINT_MODE=live to enable)",
        )
    return _parse_mode(mode_str)


def _run_response(request: Request, run_id: str) -> dict:
    description = request.app.state.graph_runner.describe(run_id)
    dag = request.app.state.dag
    nodes = []
    for run_node in description["nodes"]:
        node_id = str(run_node["node_id"])
        entry = dag.nodes.get(node_id)
        result = run_node.get("result") or {}
        error = run_node.get("error") or {}
        node = {
            "id": node_id,
            "phase": run_node.get("phase"),
            "status": run_node["status"],
            "promise_id": run_node.get("promise_id"),
            "verification": run_node.get("verification"),
            "error": error.get("message") or result.get("error"),
        }
        if run_node.get("promise_id"):
            promise = request.app.state.jobs.promise(run_node["promise_id"])
            if promise is not None:
                node["promise"] = {
                    "id": promise["id"],
                    "status": promise["status"],
                    "producer": promise.get("producer"),
                    "phase": promise.get("operation_phase"),
                    "controls": {
                        "cancellation": promise.get("cancellation_kind"),
                        "pause": promise.get("pause_kind"),
                        "progress": promise.get("progress_kind"),
                        "checkpoint": promise.get("checkpoint_kind"),
                    },
                }
        if entry is not None:
            node["type"] = type(entry.node).__name__
        if result.get("output") is not None:
            node["output"] = result["output"]
        nodes.append(node)
    status = str(description["status"])
    reboot_nodes = [
        node["id"] for node in nodes
        if node["status"] == "pending_reboot"
    ]
    response = {
        "ok": status == "succeeded",
        "accepted": status not in {"failed", "cancelled", "superseded"},
        "run_id": run_id,
        "status": status,
        "operation": description.get("operation", "resolve"),
        "mode": description["mode"],
        "pattern": description.get("pattern"),
        "created_at": description.get("created_at"),
        "updated_at": description.get("updated_at"),
        "completed_at": description.get("completed_at"),
        "controls": {
            "cancellation": "graph-run",
            "pause": "scheduler-boundary",
            "progress": "node-counts",
            "checkpoint": "run-ledger",
        },
        "summary": description["summary"],
        "needs_reboot": bool(reboot_nodes),
        "reboot_nodes": reboot_nodes,
        "nodes": nodes,
    }
    if description.get("pattern"):
        response["pattern"] = description["pattern"]
    return response


async def _resolve_cycle(
    request: Request,
    mode: ResolveMode,
    pattern: str | None = None,
):
    dag = request.app.state.dag
    node_ids = None
    if pattern is not None:
        node_ids = query_dag(dag, pattern)
        if not node_ids:
            raise HTTPException(status_code=404, detail=f"No nodes match: {pattern}")

    # Promise-backed runs may span requests and restarts, so xDS cannot keep
    # an in-memory staging transaction open for the run lifetime. Service nodes
    # own their slices and update the live cache directly; removal/unexposure
    # clears the corresponding slice explicitly.
    run_promise = request.app.state.graph_runner.start(
        mode,
        node_ids=node_ids,
        pattern=pattern,
    )
    return run_promise.id


@router.post("/resolve")
async def resolve_graph(body: ResolveRequest, request: Request):
    """Start a persistent resolve run for the entire graph."""
    try:
        mode = _effective_mode(body.mode, request)
        run_id = await _resolve_cycle(request, mode)
        return _run_response(request, run_id)
    except HTTPException:
        raise
    except Exception as error:
        logger.error("Failed to resolve graph: %s", error, exc_info=True)
        raise HTTPException(status_code=500, detail=str(error))


@router.post("/resolve/{pattern:path}")
async def resolve_subgraph(pattern: str, body: ResolveRequest, request: Request):
    """Start a persistent resolve run for matching nodes."""
    pattern = unquote(pattern)
    try:
        mode = _effective_mode(body.mode, request)
        run_id = await _resolve_cycle(request, mode, pattern)
        return _run_response(request, run_id)
    except HTTPException:
        raise
    except Exception as error:
        logger.error("Failed to resolve subgraph: %s", error, exc_info=True)
        raise HTTPException(status_code=500, detail=str(error))


@router.get("/runs/{run_id}")
async def get_graph_run(run_id: str, request: Request):
    try:
        return _run_response(request, run_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error))


@router.post("/runs/{run_id}/cancel")
async def cancel_graph_run(run_id: str, request: Request):
    try:
        request.app.state.graph_runner.cancel(run_id)
        return _run_response(request, run_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error))


@router.post("/runs/{run_id}/pause")
async def pause_graph_run(run_id: str, request: Request):
    try:
        request.app.state.graph_runner.pause(run_id)
        return _run_response(request, run_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error))


@router.post("/runs/{run_id}/resume")
async def resume_graph_run(run_id: str, request: Request):
    try:
        request.app.state.graph_runner.resume(run_id)
        return _run_response(request, run_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error))
