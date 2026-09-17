from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, Depends, Header
from fastapi.responses import StreamingResponse

from quantdesk.api.auth import Session, require_viewer

router = APIRouter(prefix="/api/v1", tags=["events"])


class EventHub:
    """Publishes versioned Server-Sent Events with cursor and resynchronization support (§15.3)."""

    def __init__(self) -> None:
        self.history: list[dict[str, Any]] = []
        self.subscribers: list[asyncio.Queue[dict[str, Any]]] = []
        self.min_retained_seq: int = 1

    def publish(
        self,
        topic: str,
        resource_version: str,
        projection_watermark: int,
        payload: dict[str, Any],
    ) -> None:
        seq = len(self.history) + 1
        event = {
            "id": str(seq),
            "topic": topic,
            "resource_version": str(resource_version),
            "projection_watermark": projection_watermark,
            "update_time_ns": time.time_ns(),
            "payload": payload,
        }
        self.history.append(event)
        # Retain last 1000 events
        if len(self.history) > 1000:
            self.history.pop(0)
            self.min_retained_seq += 1

        for queue in list(self.subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)


event_hub = EventHub()


async def sse_event_generator(last_event_id: str | None = None) -> AsyncGenerator[str, None]:
    """Generates SSE messages respecting Last-Event-ID and emitting RESYNC if cursor is stale."""
    # Check if last_event_id was provided
    if last_event_id:
        try:
            last_seq = int(last_event_id)
            if last_seq < event_hub.min_retained_seq - 1:
                # Retention window exceeded -> signal RESYNC (§15.3)
                resync_payload = {
                    "topic": "system",
                    "action": "RESYNC_REQUIRED",
                    "message": "Retention window exceeded, refresh state snapshots",
                }
                yield f"event: resync\ndata: {json.dumps(resync_payload)}\n\n"
            else:
                # Replay missed events
                for evt in event_hub.history:
                    if int(evt["id"]) > last_seq:
                        yield f"id: {evt['id']}\nevent: {evt['topic']}\ndata: {json.dumps(evt)}\n\n"
        except ValueError:
            pass

    # Initial connection confirmation
    init_msg = {"topic": "connected", "time_ns": time.time_ns()}
    yield f"event: connected\ndata: {json.dumps(init_msg)}\n\n"

    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
    event_hub.subscribers.append(queue)

    try:
        while True:
            try:
                # Wait for next event or send keepalive ping every 15s
                evt = await asyncio.wait_for(queue.get(), timeout=15.0)
                yield f"id: {evt['id']}\nevent: {evt['topic']}\ndata: {json.dumps(evt)}\n\n"
            except TimeoutError:
                # Keepalive comment
                yield ": keepalive\n\n"
    finally:
        if queue in event_hub.subscribers:
            event_hub.subscribers.remove(queue)


@router.get("/events")
async def stream_events(
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    session: Session = Depends(require_viewer),
) -> StreamingResponse:
    """Streams real-time Server-Sent Events with cursor and resync support (§15.3)."""
    return StreamingResponse(
        sse_event_generator(last_event_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
