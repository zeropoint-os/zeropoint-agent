"""Module install/remove endpoints."""

import logging
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from zeropoint_agent.module_installer import add_module
from zeropoint_agent.inode import ResolveMode

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/modules", tags=["modules"])


class ModuleAddRequest(BaseModel):
    module_id: str
    source: str
    overrides: Optional[Dict[str, str]] = None
    resolve: bool = False  # if true, resolve the module + its parents after add


@router.post("")
async def add_module_endpoint(req: ModuleAddRequest, request: Request):
    """Install a Terraform module into the DAG.

    Clones the module to inspect ``variables.tf``, wires each declared
    variable to an existing VarNode (by name) or auto-creates a new
    ``{module_id}.{varname}`` VarNode with the module's default. Adds the
    ModuleNode with all wired VarNodes as parents.

    If ``resolve`` is true, immediately resolves the new module and its
    parents in the server's configured mode.
    """
    dag = request.app.state.dag
    try:
        result = add_module(
            dag, req.module_id, req.source,
            overrides=req.overrides or {},
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("module-add failed")
        raise HTTPException(status_code=500, detail=str(e))

    response: Dict = {
        "module_id": result.module_id,
        "module_node_id": result.module_node_id,
        "created_var_nodes": result.created_var_nodes,
        "wired_existing_nodes": sorted(set(result.wired_existing_nodes)),
    }

    if req.resolve:
        mode_str = getattr(request.app.state, "default_mode", "mock")
        mode = {
            "live": ResolveMode.LIVE,
            "dry_run": ResolveMode.DRY_RUN,
            "mock": ResolveMode.MOCK,
        }.get(mode_str, ResolveMode.MOCK)

        targets = list(dict.fromkeys(
            result.created_var_nodes
            + list(set(result.wired_existing_nodes))
            + [result.module_node_id]
        ))
        statuses = dag.resolve_subset(targets, mode=mode)
        response["resolve"] = {nid: s.value for nid, s in statuses.items()}
        entry = dag.get(result.module_node_id)
        response["status"] = entry.status.value
        if entry.error:
            response["error"] = entry.error

    return response
