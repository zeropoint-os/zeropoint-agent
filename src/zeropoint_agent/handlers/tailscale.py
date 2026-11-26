"""Tailscale device lifecycle, per-app Serve ports, and accessibility matrix."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from zeropoint_agent.graph_transaction import graph_transaction
from zeropoint_agent.handlers.mutations import _node_config_dict
from zeropoint_agent.inode import NodeStatus
from zeropoint_agent.query import _get_all_descendants
from zeropoint_agent.handlers.expose import _service_protocol
from zeropoint_agent.handlers.resolve import _effective_mode, _run_response
from zeropoint_agent.integrations.tailscale.nodes import (
    SYSTEM_TAILSCALE_NODE_ID,
    TAILNET_HTTPS_PORT_MAX,
    TAILNET_HTTPS_PORT_MIN,
    SystemTailscale,
    TailnetExposure,
    normalize_user_logins,
)
from zeropoint_agent.nodes.user.exposure import (
    Exposure,
    SYSTEM_ENVOY_NODE_ID,
    canonical_local_hostname,
)
from zeropoint_agent.nodes.user.service import Service

router = APIRouter(prefix="/api/tailscale", tags=["tailscale"])


class PublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    exposure_id: str
    allowed_users: list[str] = Field(default_factory=list)
    denied_users: list[str] = Field(default_factory=list)


class UnpublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    exposure_id: str


class GlobalPolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allowed_users: list[str] = Field(default_factory=list)


class ExposurePolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    exposure_id: str
    allowed_users: list[str] = Field(default_factory=list)
    denied_users: list[str] = Field(default_factory=list)


def _start_resolve(request: Request, node_ids: list[str]) -> str:
    promise = request.app.state.graph_runner.start(
        _effective_mode("", request),
        node_ids=node_ids,
        pattern="tailscale",
    )
    return promise.id


def _tailnet_child(dag, exposure_id: str) -> tuple[str, Any] | tuple[None, None]:
    for node_id, entry in dag.nodes.items():
        if isinstance(entry.node, TailnetExposure) and exposure_id in entry.parents:
            return node_id, entry
    return None, None


def _update_policy_node(dag, node_id: str, **config: Any) -> list[str]:
    entry = dag.get(node_id)
    descendants = _get_all_descendants(dag, node_id)
    with graph_transaction(dag):
        for key, value in config.items():
            setattr(entry.node, key, value)
        for pending_id in [node_id, *descendants]:
            pending = dag.get(pending_id)
            pending.status = NodeStatus.PENDING
            pending.output = None
            pending.error = None
        if dag._store:
            dag._store.set_config(node_id, _node_config_dict(entry.node))
            dag._store.update_status(node_id, "pending")
            dag._store.invalidate_descendants(node_id)
    return descendants


def _allocate_https_port(dag) -> int:
    used = {
        entry.node.https_port
        for entry in dag.nodes.values()
        if isinstance(entry.node, TailnetExposure)
    }
    for port in range(TAILNET_HTTPS_PORT_MIN, TAILNET_HTTPS_PORT_MAX + 1):
        if port not in used:
            return port
    raise HTTPException(status_code=409, detail="No free Tailscale HTTPS ports remain")


@router.post("/expose")
async def expose_tailnet(body: PublishRequest, request: Request) -> Dict[str, Any]:
    """Publish one existing local HTTP Exposure on the machine Tailscale node."""
    dag = request.app.state.dag
    required = ["system", "system/docker", SYSTEM_ENVOY_NODE_ID]
    missing = [node_id for node_id in required if node_id not in dag.nodes]
    if missing:
        raise HTTPException(
            status_code=409,
            detail=f"Tailscale requires existing system ingress nodes: {missing}",
        )
    if (
        SYSTEM_TAILSCALE_NODE_ID in dag.nodes
        and not isinstance(dag.get(SYSTEM_TAILSCALE_NODE_ID).node, SystemTailscale)
    ):
        raise HTTPException(
            status_code=409,
            detail=f"{SYSTEM_TAILSCALE_NODE_ID!r} is not a SystemTailscale node",
        )

    exposure_entry = dag.nodes.get(body.exposure_id)
    if exposure_entry is None:
        raise HTTPException(status_code=404, detail=f"Exposure not found: {body.exposure_id}")
    if not isinstance(exposure_entry.node, Exposure):
        raise HTTPException(status_code=400, detail=f"{body.exposure_id!r} is not an Exposure")

    service_id = ""
    service_entry = None
    for parent_id in exposure_entry.parents:
        candidate = dag.nodes.get(parent_id)
        if candidate is not None and isinstance(candidate.node, Service):
            service_id = parent_id
            service_entry = candidate
            break
    if service_entry is None or _service_protocol(service_entry) != "http":
        raise HTTPException(status_code=400, detail="Tailnet publication supports HTTP exposures only")

    existing_id, _ = _tailnet_child(dag, body.exposure_id)
    if existing_id is not None:
        raise HTTPException(status_code=409, detail=f"Tailnet Exposure already exists: {existing_id}")

    try:
        allowed_users = normalize_user_logins(body.allowed_users)
        denied_users = normalize_user_logins(body.denied_users)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    node_id = f"{body.exposure_id}/tailnet"
    root_created = SYSTEM_TAILSCALE_NODE_ID not in dag.nodes
    with graph_transaction(dag):
        https_port = _allocate_https_port(dag)
        if root_created:
            dag.add(
                SYSTEM_TAILSCALE_NODE_ID,
                SystemTailscale(),
                parents=required,
                perms="rw-",
                tags={"tailscale", "system"},
            )
        dag.add(
            node_id,
            TailnetExposure(
                slug=exposure_entry.node.name,
                https_port=https_port,
                allowed_users=allowed_users,
                denied_users=denied_users,
            ),
            parents=[body.exposure_id, SYSTEM_TAILSCALE_NODE_ID],
            perms="rw-",
            tags={"exposure", "tailnet", "tailscale"},
        )
    run_id = _start_resolve(
        request,
        [service_id, body.exposure_id, SYSTEM_TAILSCALE_NODE_ID, node_id],
    )
    return {
        "ok": True,
        "node_id": node_id,
        "root_created": root_created,
        "https_port": https_port,
        "allowed_users": allowed_users,
        "denied_users": denied_users,
        "run_id": run_id,
    }


@router.put("/policy")
async def update_global_policy(
    body: GlobalPolicyRequest,
    request: Request,
) -> Dict[str, Any]:
    """Update the machine-wide allowlist, including legacy read-only roots."""
    dag = request.app.state.dag
    entry = dag.nodes.get(SYSTEM_TAILSCALE_NODE_ID)
    if entry is None:
        raise HTTPException(status_code=404, detail="system/tailscale is not enabled")
    if not isinstance(entry.node, SystemTailscale):
        raise HTTPException(status_code=409, detail="system/tailscale has an unexpected type")
    try:
        allowed_users = normalize_user_logins(body.allowed_users)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    descendants = _update_policy_node(
        dag,
        SYSTEM_TAILSCALE_NODE_ID,
        allowed_users=allowed_users,
    )
    tailnet_nodes = [
        node_id for node_id in descendants
        if isinstance(dag.get(node_id).node, TailnetExposure)
    ]
    run_id = _start_resolve(
        request,
        [SYSTEM_TAILSCALE_NODE_ID, *tailnet_nodes],
    )
    return {
        "ok": True,
        "node_id": SYSTEM_TAILSCALE_NODE_ID,
        "allowed_users": allowed_users,
        "invalidated": tailnet_nodes,
        "run_id": run_id,
    }


@router.put("/expose/policy")
async def update_exposure_policy(
    body: ExposurePolicyRequest,
    request: Request,
) -> Dict[str, Any]:
    """Update one Tailnet Exposure allow/deny policy and reconcile xDS."""
    dag = request.app.state.dag
    node_id, entry = _tailnet_child(dag, body.exposure_id)
    if node_id is None or entry is None:
        raise HTTPException(status_code=404, detail="Tailnet Exposure not found")
    try:
        allowed_users = normalize_user_logins(body.allowed_users)
        denied_users = normalize_user_logins(body.denied_users)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    _update_policy_node(
        dag,
        node_id,
        allowed_users=allowed_users,
        denied_users=denied_users,
    )
    run_id = _start_resolve(request, [node_id])
    return {
        "ok": True,
        "node_id": node_id,
        "allowed_users": allowed_users,
        "denied_users": denied_users,
        "run_id": run_id,
    }


@router.post("/unexpose")
async def unexpose_tailnet(body: UnpublishRequest, request: Request) -> Dict[str, Any]:
    """Remove remote publication while preserving the local Exposure."""
    dag = request.app.state.dag
    node_id, _ = _tailnet_child(dag, body.exposure_id)
    if node_id is None:
        return {"ok": True, "deleted": [], "local_preserved": True}
    promise = request.app.state.graph_runner.start_remove(
        [node_id],
        _effective_mode("", request),
        pattern=node_id,
    )
    response = _run_response(request, promise.id)
    response.update({"deleted": [node_id], "local_preserved": True})
    return response


@router.delete("")
async def disable_tailscale(request: Request) -> Dict[str, Any]:
    """Remove all remote children before the machine Tailscale runtime."""
    dag = request.app.state.dag
    if SYSTEM_TAILSCALE_NODE_ID not in dag.nodes:
        return {"ok": True, "targets": []}
    children = [
        node_id
        for node_id, entry in dag.nodes.items()
        if isinstance(entry.node, TailnetExposure)
        and SYSTEM_TAILSCALE_NODE_ID in entry.parents
    ]
    targets = [*children, SYSTEM_TAILSCALE_NODE_ID]
    promise = request.app.state.graph_runner.start_remove(
        targets,
        _effective_mode("", request),
        pattern="tailscale-disable",
    )
    response = _run_response(request, promise.id)
    response["targets"] = targets
    return response


@router.get("/matrix")
async def accessibility_matrix(request: Request) -> Dict[str, Any]:
    """Return local and tailnet reachability derived only from graph state."""
    dag = request.app.state.dag
    machine_name = str(getattr(request.app.state.identity, "name", ""))
    rows = []
    for exposure_id, entry in dag.nodes.items():
        if not isinstance(entry.node, Exposure):
            continue
        service_entry = next(
            (
                dag.nodes[parent_id]
                for parent_id in entry.parents
                if parent_id in dag.nodes
                and isinstance(dag.nodes[parent_id].node, Service)
            ),
            None,
        )
        protocol = _service_protocol(service_entry) if service_entry is not None else ""
        tailnet_id, tailnet_entry = _tailnet_child(dag, exposure_id)
        tailnet_output = tailnet_entry.output if tailnet_entry is not None else None
        rows.append({
            "exposure_id": exposure_id,
            "slug": entry.node.name,
            "local": {
                "available": True,
                "protocol": protocol,
                "url": (
                    f"http://{canonical_local_hostname(entry.node.name, machine_name)}/"
                    if protocol == "http"
                    else None
                ),
                "host_port": entry.node.host_port or None,
            },
            "tailnet": {
                "configured": tailnet_entry is not None,
                "node_id": tailnet_id,
                "node_hostname": getattr(tailnet_output, "node_hostname", None),
                "https_port": (
                    tailnet_entry.node.https_port if tailnet_entry is not None else None
                ),
                "status": getattr(tailnet_output, "publication_status", None),
                "effective_allowed_users": getattr(
                    tailnet_output,
                    "effective_allowed_users",
                    None,
                ),
                "url": getattr(tailnet_output, "url", None),
                "healthy": bool(getattr(tailnet_output, "healthy", False)),
                "error": getattr(tailnet_output, "error", None),
            },
        })
    return {"ok": True, "rows": rows}
