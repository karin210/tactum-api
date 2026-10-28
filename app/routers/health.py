from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.errors import ApiError

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
async def health() -> dict[str, str]:
    """Liveness: the process is up. Doesn't touch the database."""
    return {"status": "ok"}


@router.get("/db")
async def database_health(session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, str]:
    """Readiness: the API can reach PostgreSQL."""
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:
        raise ApiError(
            503, "database_unavailable", "The database is not reachable.", {"Retry-After": "30"}
        ) from exc
    return {"status": "ok"}
