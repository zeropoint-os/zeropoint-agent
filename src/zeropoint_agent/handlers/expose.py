"""Expose / Unexpose — declare a Service as LAN-visible.

The new model: an `Exposure` node is purely a child of a `Service`
node. Its presence means "expose this". No data-parent / link-target
mechanics; the Service it's parented to already knows the port,
protocol, container, etc.

The Service node itself is created by an OutputVar at resolve time
when its value contains a {port, protocol} bundle. The user can't
create Services; they appear when a module's terraform outputs a
matching shape.

Expose:
  POST /api/expose {service_id, name?, host_port?}
    - validates service_id resolves to a Service node
    - picks a default `name` (module leaf name) if not supplied
    - allocates a host_port for tcp services
    - creates `<service_id>/<exposure-leaf>` parented to the Service
    - wrapped in graph_transaction
  Then triggers a full resolve so the new exposure flows to Envoy.

Unexpose:
  POST /api/unexpose {service_id}
    - deletes every Exposure child of the Service (typically just one)
    - triggers a full resolve so the xDS cache drops the slice
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional, Set

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from zeropoint_agent.graph_transaction import graph_transaction
from zeropoint_agent.nodes.config.namespace import Namespace
from zeropoint_agent.nodes.user.exposure import Exposure
from zeropoint_agent.nodes.user.service import Service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["expose"])

TCP_PORT_MIN = 10000
TCP_PORT_MAX = 60000

_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class ExposeRequest(BaseModel):
    service_id: str
    name: Optional[str] = None
    host_port: Optional[int] = None


class UnexposeRequest(BaseModel):
    service_id: str


def _service_protocol(service_entry) -> str:
    out = service_entry.output
    p = getattr(out, "protocol", None)
    if p is None and isinstance(out, dict):
        p = out.get("protocol")
    return str(p) if p else ""


def _module_leaf(dag, service_id: str) -> str:
    """Find the owning module namespace and return its leaf segment.

    Walks ancestry: Service -> OutputVar -> module namespace.
    """
    visited: Set[str] = set()
    queue = [service_id]
    while queue:
        nid = queue.pop()
        if nid in visited:
            continue
        visited.add(nid)
        entry = dag.nodes.get(nid)
        if entry is None:
            continue
        for pid in entry.parents:
            p = dag.nodes.get(pid)
            if p is None:
                continue
            if isinstance(p.node, Namespace) and pid.startswith("modules/"):
                return pid.rsplit("/", 1)[-1]
            queue.append(pid)
    # Fallback: leaf of the service itself.
    return service_id.rsplit("/", 1)[-1]


def _existing_exposure_names(dag) -> Set[str]:
    return {
        e.node.name
        for e in dag.nodes.values()
        if isinstance(e.node, Exposure) and e.node.name
    }


def _allocate_host_port(dag) -> int:
    used: Set[int] = set()
    for entry in dag.nodes.values():
        if isinstance(entry.node, Exposure) and entry.node.host_port:
            used.add(int(entry.node.host_port))
    for p in range(TCP_PORT_MIN, TCP_PORT_MAX + 1):
        if p not in used:
            return p
    raise HTTPException(status_code=500, detail="No free TCP host ports left")


def _pick_default_name(dag, service_id: str) -> str:
    """Default Exposure name = module leaf. If taken, append the service leaf."""
    used = _existing_exposure_names(dag)
    base = _module_leaf(dag, service_id)
    if base not in used:
        return base
    svc_leaf = service_id.rsplit("/", 1)[-1]
    candidate = f"{base}_{svc_leaf}"
    if candidate not in used:
        return candidate
    i = 2
    while True:
        c = f"{candidate}_{i}"
        if c not in used:
            return c
        i += 1


@router.post("/expose")
async def expose(body: ExposeRequest, request: Request) -> Dict[str, Any]:
    dag = request.app.state.dag

    svc_entry = dag.nodes.get(body.service_id)
    if svc_entry is None:
        raise HTTPException(status_code=404,
                            detail=f"Service not found: {body.service_id}")
    if not isinstance(svc_entry.node, Service):
        raise HTTPException(
            status_code=400,
            detail=f"{body.service_id!r} is not a Service (type={type(svc_entry.node).__name__})")

    name = body.name or _pick_default_name(dag, body.service_id)
    if not _NAME_RE.match(name):
        raise HTTPException(
            status_code=400,
            detail=f"Exposure name {name!r} must be alphanumeric (with _ or -).")

    protocol = _service_protocol(svc_entry) or "tcp"
    host_port = int(body.host_port or 0)
    if protocol == "tcp" and host_port == 0:
        host_port = _allocate_host_port(dag)

    exposure_id = f"{body.service_id}/exposure_{name}"
    if exposure_id in dag.nodes:
        raise HTTPException(status_code=409,
                            detail=f"Exposure {exposure_id!r} already exists.")

    try:
        with graph_transaction(dag):
            dag.add(
                exposure_id,
                Exposure(name=name, host_port=host_port),
                parents=[body.service_id],
                perms="r--",
                tags={"exposure"},
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("expose failed for %s", body.service_id)
        raise HTTPException(status_code=500, detail=str(e)) from e

    # Resolving the Service runs its xDS publish + network attach in
    # the same step. No external network-attach call needed here —
    # Service.resolve owns that responsibility.
    await _trigger_resolve(request)

    return {
        "ok": True,
        "exposure_id": exposure_id,
        "name": name,
        "protocol": protocol,
        "host_port": host_port,
        "service_id": body.service_id,
    }


@router.post("/unexpose")
async def unexpose(body: UnexposeRequest, request: Request) -> Dict[str, Any]:
    dag = request.app.state.dag

    to_delete = [
        nid for nid, entry in dag.nodes.items()
        if isinstance(entry.node, Exposure) and body.service_id in entry.parents
    ]
    if not to_delete:
        return {"ok": True, "deleted": []}

    try:
        with graph_transaction(dag):
            for nid in to_delete:
                dag.remove(nid)
    except Exception as e:
        logger.exception("unexpose failed for %s", body.service_id)
        raise HTTPException(status_code=500, detail=str(e)) from e

    await _trigger_resolve(request)

    return {"ok": True, "deleted": to_delete}


async def _trigger_resolve(request: Request) -> None:
    """Run a full resolve cycle so the graph state reflects in Envoy."""
    from zeropoint_agent.handlers.resolve import _resolve_cycle, _effective_mode
    try:
        mode = _effective_mode("", request)
        await _resolve_cycle(request, mode)
    except Exception as e:
        logger.warning("post-expose resolve failed (continuing): %s", e)
