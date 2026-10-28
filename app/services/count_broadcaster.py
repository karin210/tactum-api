import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

import asyncpg

logger = logging.getLogger(__name__)

# Must match the channel used by the trigger created in the first Alembic migration.
CHANNEL = "reservations_changed"
RECONNECT_DELAY_SECONDS = 5


class ReservationCountBroadcaster:
    """Pushes the live reservation count to every open SSE connection.

    One dedicated connection LISTENs on the `reservations_changed` channel, which a
    database trigger notifies after any change to the table. On each notification the
    count is re-read once and handed to all subscribers. This replaces the MongoDB
    change stream the Next.js route used.
    """

    def __init__(self, connect: Callable[[], Awaitable[asyncpg.Connection]]) -> None:
        self._connect = connect
        self._subscribers: set[asyncio.Queue[int]] = set()
        self._latest: int | None = None
        self._changed = asyncio.Event()
        self._connected = False
        self._task: asyncio.Task[None] | None = None

    @property
    def is_connected(self) -> bool:
        return self._connected

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="reservation-count-broadcaster")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    def subscribe(self) -> asyncio.Queue[int]:
        queue: asyncio.Queue[int] = asyncio.Queue(maxsize=1)
        if self._latest is not None:
            queue.put_nowait(self._latest)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[int]) -> None:
        self._subscribers.discard(queue)

    async def _run(self) -> None:
        # Reconnects forever, so a database restart or RDS failover only pauses the counter.
        while True:
            try:
                await self._listen()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Reservation count listener failed; retrying")
            finally:
                self._connected = False
            await asyncio.sleep(RECONNECT_DELAY_SECONDS)

    async def _listen(self) -> None:
        connection = await self._connect()
        try:
            await connection.add_listener(CHANNEL, self._on_notification)
            # Wakes the loop below if the connection drops, so it can reconnect.
            connection.add_termination_listener(lambda _connection: self._changed.set())
            self._connected = True
            # Publish once right away, in case the count changed while disconnected.
            await self._publish(connection)
            while not connection.is_closed():
                await self._changed.wait()
                self._changed.clear()
                await self._publish(connection)
        finally:
            await connection.close()

    def _on_notification(self, *_args: object) -> None:
        self._changed.set()

    async def _publish(self, connection: asyncpg.Connection) -> None:
        count: int = await connection.fetchval("SELECT count(*) FROM reservations")
        self._latest = count
        for queue in self._subscribers:
            # A slow client only needs the newest count, so replace anything not yet sent.
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(count)
