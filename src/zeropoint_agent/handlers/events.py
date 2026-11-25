"""Durable event ledger REST and Server-Sent Events resources."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncIterator, Dict, Optional

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/api/events", tags=["events"])


def _encode_sse_event(event: Dict[str, Any]) -> str:
    data = json.dumps(event, separators=(",", ":"), sort_keys=True)
    return (
        f"id: {event['sequence']}\n"
        f"event: {event['event_type']}\n"
        f"data: {data}\n\n"
    )


@router.get("")
async def list_events(
    request: Request,
    after: int = 0,
    limit: int = 100,
    event_type: Optional[str] = None,
    aggregate_type: Optional[str] = None,
    aggregate_id: Optional[str] = None,
):
    events = request.app.state.store.list_events(
        after_sequence=after,
        limit=limit,
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
    )
    return {
        "after": max(0, after),
        "next_sequence": events[-1]["sequence"] if events else max(0, after),
        "events": events,
    }


@router.get("/stream")
async def stream_events(
    request: Request,
    after: int = 0,
    event_type: Optional[str] = None,
    aggregate_type: Optional[str] = None,
    aggregate_id: Optional[str] = None,
):
    last_event_id = request.headers.get("last-event-id")
    if last_event_id:
        try:
            after = max(after, int(last_event_id))
        except ValueError:
            pass

    async def generate() -> AsyncIterator[str]:
        cursor = max(0, after)
        heartbeat_at = time.monotonic() + 15
        yield "retry: 1000\n\n"
        while not await request.is_disconnected():
            events = await asyncio.to_thread(
                request.app.state.store.list_events,
                after_sequence=cursor,
                limit=100,
                event_type=event_type,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
            )
            if events:
                for event in events:
                    cursor = int(event["sequence"])
                    yield _encode_sse_event(event)
                heartbeat_at = time.monotonic() + 15
                continue
            if time.monotonic() >= heartbeat_at:
                yield f": keepalive {cursor}\n\n"
                heartbeat_at = time.monotonic() + 15
            await asyncio.sleep(0.25)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
