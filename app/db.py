import ssl
from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import DatabaseSettings, get_database_settings


def ssl_option(settings: DatabaseSettings) -> ssl.SSLContext | str | bool:
    """Translate the SSL setting into the value asyncpg expects for its `ssl` argument."""
    if settings.database_ssl == "disable":
        return False
    if settings.database_ssl == "verify-full" and settings.database_ssl_root_cert:
        return ssl.create_default_context(cafile=settings.database_ssl_root_cert)
    return settings.database_ssl


@lru_cache
def get_engine() -> AsyncEngine:
    settings = get_database_settings()
    return create_async_engine(
        settings.sqlalchemy_url,
        connect_args={"ssl": ssl_option(settings)},
        pool_size=5,
        max_overflow=5,
        # Drops connections that died during an RDS failover or restart.
        pool_pre_ping=True,
    )


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        yield session
