"""Canonical GraphRun REST resources, suitable for direct MCP mapping."""

import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from zeropoint_agent.graph_transaction import graph_transaction
from zeropoint_agent.handlers.mutations import delete_nodes
from zeropoint_agent.handlers.resolve import (
    _effective_mode,
    _resolve_cycle,
    _run_response,
)
from zeropoint_agent.inode import NodeStatus
from zeropoint_agent.nodes.system.reboot import Reboot, current_boot_id

router = APIRouter(prefix="/api/runs", tags=["runs"])


class ResolveRunRequest(BaseModel):
    mode: str = ""
    pattern: Optional[str] = None


class RemoveRunRequest(BaseModel):
    pattern: str


class RebootRunRequest(BaseModel):
    mode: str = ""


@router.post("/resolve", status_code=202)
async def create_resolve_run(body: ResolveRunRequest, request: Request):
    mode = _effective_mode(body.mode, request)
    run_id = await _resolve_cycle(request, mode, body.pattern)
    return _run_response(request, run_id)


@router.post("/remove", status_code=202)
async def create_remove_run(body: RemoveRunRequest, request: Request):
    return await delete_nodes(body.pattern, request)


@router.post("/reboot", status_code=202)
async def create_reboot_run(body: RebootRunRequest, request: Request):
    dag = request.app.state.dag
    if "system" not in dag.nodes:
        raise HTTPException(
            status_code=409,
            detail="system root is not present in the graph",
        )
    request_id = uuid.uuid4().hex
    requested_boot_id = current_boot_id()
    with graph_transaction(dag):
        if "system/reboot" not in dag.nodes:
            dag.add(
                "system/reboot",
                Reboot(
                    request_id=request_id,
                    requested_boot_id=requested_boot_id,
                ),
                parents=["system"],
                perms="r--",
            )
        else:
            entry = dag.get("system/reboot")
            entry.node.request_id = request_id
            entry.node.requested_boot_id = requested_boot_id
            entry.status = NodeStatus.PENDING
            entry.output = None
            entry.error = None
            request.app.state.store.set_config(
                "system/reboot",
                {
                    "request_id": request_id,
                    "requested_boot_id": requested_boot_id,
                },
            )
            request.app.state.store.update_status(
                "system/reboot",
                NodeStatus.PENDING.value,
            )
    mode = _effective_mode(body.mode, request)
    run = request.app.state.graph_runner.start(
        mode,
        node_ids=["system", "system/reboot"],
        pattern="system/reboot",
    )
    return _run_response(request, run.id)


@router.get("")
async def list_runs(
    request: Request,
    status: Optional[str] = None,
    limit: int = 100,
):
    limit = max(1, min(limit, 500))
    return {
        "runs": request.app.state.store.list_graph_runs(
            status=status,
            limit=limit,
        )
    }


@router.get("/{run_id}")
async def get_run(run_id: str, request: Request):
    try:
        return _run_response(request, run_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error))


@router.post("/{run_id}/cancel")
async def cancel_run(run_id: str, request: Request):
    try:
        request.app.state.graph_runner.cancel(run_id)
        return _run_response(request, run_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error))


@router.post("/{run_id}/pause")
async def pause_run(run_id: str, request: Request):
    try:
        request.app.state.graph_runner.pause(run_id)
        return _run_response(request, run_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error))


@router.post("/{run_id}/resume")
async def resume_run(run_id: str, request: Request):
    try:
        request.app.state.graph_runner.resume(run_id)
        return _run_response(request, run_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error))
