import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import partial

import asyncpg
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.db import create_pool, ssl_option
from app.errors import RequestIdMiddleware, register_error_handlers
from app.routers import health, leads, payments, reservations, webhooks
from app.services.count_broadcaster import ReservationCountBroadcaster

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    pool = await create_pool(settings)
    app.state.pool = pool
    broadcaster = ReservationCountBroadcaster(
        connect=partial(asyncpg.connect, settings.database_url, ssl=ssl_option(settings))
    )
    app.state.count_broadcaster = broadcaster
    broadcaster.start()
    try:
        yield
    finally:
        await broadcaster.stop()
        await pool.close()


def create_app() -> FastAPI:
    settings = get_settings()
    # Interactive docs are handy locally but advertise the API surface in production.
    docs_enabled = not settings.is_production
    app = FastAPI(
        title="Tactum API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(RequestIdMiddleware)
    register_error_handlers(app)

    for module in (health, leads, payments, reservations, webhooks):
        app.include_router(module.router)

    return app


app = create_app()
