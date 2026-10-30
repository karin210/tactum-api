import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated

import asyncpg
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.db import get_connection
from app.dependencies import get_count_broadcaster
from app.errors import ApiError
from app.schemas.orders import OrderCount
from app.services.count_broadcaster import OrderCountBroadcaster
from app.services.orders import count_orders

# The URL keeps its old name so the website's counter keeps working.
router = APIRouter(prefix="/reservations", tags=["orders"])

# Comment lines keep proxies and load balancers from closing an idle stream.
KEEP_ALIVE_SECONDS = 15


@router.get("/count")
async def order_count(
    connection: Annotated[asyncpg.Connection, Depends(get_connection)],
) -> OrderCount:
    return OrderCount(count=await count_orders(connection))


@router.get("/count/stream", response_class=StreamingResponse)
async def order_count_stream(
    broadcaster: Annotated[OrderCountBroadcaster, Depends(get_count_broadcaster)],
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
