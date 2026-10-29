import ssl
from collections.abc import AsyncIterator
from typing import cast

import asyncpg
from fastapi import Request

from app.config import DatabaseSettings


def ssl_option(settings: DatabaseSettings) -> ssl.SSLContext | str | bool:
    """Translate the SSL setting into the value asyncpg expects for its `ssl` argument."""
    if settings.database_ssl == "disable":
        return False
    if settings.database_ssl == "verify-full" and settings.database_ssl_root_cert:
        return ssl.create_default_context(cafile=settings.database_ssl_root_cert)
    return settings.database_ssl


async def create_pool(settings: DatabaseSettings) -> asyncpg.Pool:
    return await asyncpg.create_pool(
        settings.database_url,
        ssl=ssl_option(settings),
        # Starts without opening a connection, so the API still boots while the database is
        # down and /health/db can report it.
        min_size=0,
        max_size=10,
        # Closes idle connections before an RDS failover or restart can leave them dead.
        max_inactive_connection_lifetime=300,
    )


def get_pool(request: Request) -> asyncpg.Pool:
    return cast(asyncpg.Pool, request.app.state.pool)


async def get_connection(request: Request) -> AsyncIterator[asyncpg.Connection]:
    """One connection from the pool for the length of the request."""
    async with get_pool(request).acquire() as connection:
        yield connection
