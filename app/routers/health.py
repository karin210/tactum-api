from typing import Annotated

import asyncpg
from fastapi import APIRouter, Depends

from app.db import get_pool
from app.errors import ApiError

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
async def health() -> dict[str, str]:
    """Liveness: the process is up. Doesn't touch the database."""
    return {"status": "ok"}


@router.get("/db")
async def database_health(pool: Annotated[asyncpg.Pool, Depends(get_pool)]) -> dict[str, str]:
    """Readiness: the API can reach PostgreSQL."""
    try:
        await pool.execute("SELECT 1")
    except Exception as exc:
        raise ApiError(
            503, "database_unavailable", "The database is not reachable.", {"Retry-After": "30"}
        ) from exc
    return {"status": "ok"}
