from typing import Annotated

import asyncpg
from fastapi import APIRouter, Depends, Response, status

from app.db import get_connection
from app.schemas.leads import LeadIn, LeadOut
from app.services.leads import upsert_lead

router = APIRouter(prefix="/leads", tags=["leads"])


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses={
        200: {"model": LeadOut, "description": "An existing user with this email was updated."}
    },
)
async def create_lead(
    lead: LeadIn,
    response: Response,
    connection: Annotated[asyncpg.Connection, Depends(get_connection)],
) -> LeadOut:
    """Subscribe someone to news. Re-subscribing with the same email updates their details."""
    result = await upsert_lead(connection, lead)
    if not result.created:
        response.status_code = status.HTTP_200_OK
    return LeadOut(id=result.id, email=result.email, user_type=result.user_type)
