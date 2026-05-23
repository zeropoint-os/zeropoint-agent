"""Module install/remove endpoints."""

import logging
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from zeropoint_agent.graph_transaction import graph_transaction
from zeropoint_agent.module_installer import add_module
from zeropoint_agent.inode import ResolveMode

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
    """Install a Terraform module into the DAG.

    Requires `w` on the target parent namespace (default ``modules``).

    Clones the module to inspect ``variables.tf``, wires each declared
    variable to an existing Var (by name) or auto-creates a new
    ``{namespace}/{varname}`` Var with the module's default. Adds the
    Terraform under the namespace.

    The structural install (~15 dag.add calls) is wrapped in a
    graph_transaction so any failure halfway through rolls back the
    whole subgraph — no half-installed modules left in the store.

    If ``resolve`` is true, immediately resolves the new module and its
    parents. A resolve failure is NOT rolled back: the install is
    complete and the user can fix config + retry.
    """
    dag = request.app.state.dag

    # Permission check on the parent namespace.
    if req.parent_namespace not in dag.nodes:
        raise HTTPException(
            status_code=404,
            detail=f"parent namespace {req.parent_namespace!r} not found")
    eff = dag.effective_perms(req.parent_namespace)
    if "w" not in eff:
        raise HTTPException(
            status_code=403,
            detail=(f"parent namespace {req.parent_namespace!r} is not "
                    f"writable (cannot install module; effective perms: {eff})")
        )

    try:
        with graph_transaction(dag):
            result = add_module(
                dag, req.module_id, req.source,
                overrides=req.overrides or {},
                parent_namespace=req.parent_namespace,
            )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("module-add failed")
        raise HTTPException(status_code=500, detail=str(e))

    response: Dict = {
        "module_id": result.module_id,
        "namespace_id": result.namespace_id,
        "terraform_id": result.terraform_id,
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
        ))
        statuses = dag.resolve_subset(targets, mode=mode)
        response["resolve"] = {nid: s.value for nid, s in statuses.items()}
        entry = dag.get(result.terraform_id)
        response["status"] = entry.status.value
        if entry.error:
            response["error"] = entry.error

    return response

    response: Dict = {
        "module_id": result.module_id,
        "namespace_id": result.namespace_id,
        "terraform_id": result.terraform_id,
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
        ))
        statuses = dag.resolve_subset(targets, mode=mode)
        response["resolve"] = {nid: s.value for nid, s in statuses.items()}
        entry = dag.get(result.terraform_id)
        response["status"] = entry.status.value
        if entry.error:
            response["error"] = entry.error

    return response
