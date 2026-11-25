"""Fleet discovery metadata."""

from fastapi import APIRouter, Request

from zeropoint_agent.identity import build_meta

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/meta")
async def get_meta(request: Request):
    return build_meta(
        request.app.state.identity,
        request.app.state.store,
        port=request.app.state.api_port,
    )
