"""Health and status endpoints."""

from datetime import datetime, timezone

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def health(request: Request):
    dag = request.app.state.dag
    node_count = len(dag.nodes)
    statuses = {}
    for nid, entry in dag.nodes.items():
        s = entry.status.value
        statuses[s] = statuses.get(s, 0) + 1
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "graph": {"nodes": node_count, "statuses": statuses},
    }
