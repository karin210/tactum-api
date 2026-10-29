from dataclasses import dataclass
from uuid import UUID

import asyncpg

from app.schemas.leads import LeadIn

# On a conflict, a NULL parameter means "not provided": COALESCE keeps the saved value instead
# of erasing it. user_type is left out of the UPDATE, so a customer is never downgraded to a
# lead. xmax is 0 only on a freshly inserted row, which tells an insert from an update.
UPSERT_LEAD = """
    INSERT INTO users (email, first_name, last_name, phone, city, user_type)
    VALUES ($1, $2, $3, $4, COALESCE($5::text, 'Other'), 'lead')
    ON CONFLICT (email) DO UPDATE SET
        first_name = COALESCE(EXCLUDED.first_name, users.first_name),
        last_name = COALESCE(EXCLUDED.last_name, users.last_name),
        phone = COALESCE(EXCLUDED.phone, users.phone),
        city = COALESCE($5::text, users.city),
        updated_at = now()
    RETURNING id, email, user_type, (xmax = 0) AS created
"""


@dataclass(frozen=True)
class LeadResult:
    id: UUID
    email: str
    user_type: str
    created: bool


async def upsert_lead(connection: asyncpg.Connection, lead: LeadIn) -> LeadResult:
    """Create the lead, or update the existing user with that email.

    Optional fields left empty don't erase values saved earlier, and an existing
    customer is never downgraded back to a lead.
    """
    row = await connection.fetchrow(
        UPSERT_LEAD, lead.email, lead.first_name, lead.last_name, lead.phone, lead.city
    )
    assert row is not None  # An upsert with RETURNING always yields a row.
    return LeadResult(
        id=row["id"], email=row["email"], user_type=row["user_type"], created=row["created"]
    )
