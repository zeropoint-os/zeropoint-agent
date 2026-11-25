"""Module install/remove endpoints."""

import logging
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from zeropoint_agent.dag import NodeExists
from zeropoint_agent.graph_transaction import graph_transaction
from zeropoint_agent.inode import ResolveMode
from zeropoint_agent.nodes.user.module_definition import Module

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/modules", tags=["modules"])


class ModuleAddRequest(BaseModel):
    module_id: str
    source: str
    overrides: Optional[Dict[str, str]] = None
    resolve: bool = False  # if true, resolve the module + its parents after add
    parent_namespace: str = "modules"


@router.post("")
async def add_module_endpoint(req: ModuleAddRequest, request: Request):
    """Create a Module producer and optionally start its durable GraphRun."""
    dag = request.app.state.dag

    # Permission check on the parent namespace.
    if req.parent_namespace not in dag.nodes:
        raise HTTPException(
            status_code=404,
            detail=f"parent namespace {req.parent_namespace!r} not found")
    if dag._store and dag._store.is_removal_pending(req.parent_namespace):
        raise HTTPException(
            status_code=409,
            detail=f"parent namespace {req.parent_namespace!r} is pending removal",
        )
    eff = dag.effective_perms(req.parent_namespace)
    if "w" not in eff:
        raise HTTPException(
            status_code=403,
            detail=(f"parent namespace {req.parent_namespace!r} is not "
                    f"writable (cannot install module; effective perms: {eff})")
        )

    try:
        with graph_transaction(dag):
            namespace_id = f"{req.parent_namespace}/{req.module_id}"
            dag.add(
                namespace_id,
                Module(
                    name=req.module_id,
                    source=req.source,
                    overrides=req.overrides or {},
                ),
                parents=[req.parent_namespace],
                perms="rwd",
            )
    except NodeExists as error:
        raise HTTPException(status_code=409, detail=str(error))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("module-add failed")
        raise HTTPException(status_code=500, detail=str(e))

    response: Dict = {
        "module_id": req.module_id,
        "namespace_id": namespace_id,
        "status": "pending",
    }

    if req.resolve:
        mode_str = getattr(request.app.state, "default_mode", "mock")
        mode = {
            "live": ResolveMode.LIVE,
            "dry_run": ResolveMode.DRY_RUN,
            "mock": ResolveMode.MOCK,
        }.get(mode_str, ResolveMode.MOCK)

        run_promise = request.app.state.graph_runner.start(
            mode,
            node_ids=[req.parent_namespace, namespace_id],
            pattern=namespace_id,
        )
        run_id = run_promise.id
        run = request.app.state.graph_runner.describe(run_id)
        response["run_id"] = run_id
        response["status"] = run["status"]

    return response
