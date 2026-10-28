import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.dependencies import get_count_broadcaster
from app.errors import ApiError
from app.schemas.reservations import ReservationCount
from app.services.count_broadcaster import ReservationCountBroadcaster
from app.services.reservations import count_reservations

router = APIRouter(prefix="/reservations", tags=["reservations"])

# Comment lines keep proxies and load balancers from closing an idle stream.
KEEP_ALIVE_SECONDS = 15


@router.get("/count")
async def reservation_count(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ReservationCount:
    return ReservationCount(count=await count_reservations(session))


@router.get("/count/stream", response_class=StreamingResponse)
async def reservation_count_stream(
    broadcaster: Annotated[ReservationCountBroadcaster, Depends(get_count_broadcaster)],
) -> StreamingResponse:
    """Server-Sent Events: sends `{"count": n}` now and again whenever it changes."""
    if not broadcaster.is_connected:
        raise ApiError(
            503,
            "counter_unavailable",
            "The counter is not available right now.",
            {"Retry-After": "30"},
        )

    async def events() -> AsyncIterator[str]:
        queue = broadcaster.subscribe()
        # Starlette stops this generator when the client disconnects, which runs `finally`.
        try:
            while True:
                try:
                    count = await asyncio.wait_for(queue.get(), timeout=KEEP_ALIVE_SECONDS)
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield f"data: {json.dumps({'count': count})}\n\n"
        finally:
            broadcaster.unsubscribe(queue)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        # X-Accel-Buffering stops nginx-style proxies from buffering the stream.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
