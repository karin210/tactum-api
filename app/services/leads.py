from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, literal_column
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.schemas.leads import LeadIn


@dataclass(frozen=True)
class LeadResult:
    id: UUID
    email: str
    user_type: str
    created: bool


async def upsert_lead(session: AsyncSession, lead: LeadIn) -> LeadResult:
    """Create the lead, or update the existing user with that email.

    Optional fields left empty don't erase values saved earlier, and an existing
    customer is never downgraded back to a lead.
    """
    provided = {
        key: value for key, value in lead.model_dump(by_alias=False).items() if value is not None
    }
    statement = insert(User).values(**provided, user_type="lead")
    statement = statement.on_conflict_do_update(
        index_elements=[User.email],
        set_={**{key: statement.excluded[key] for key in provided}, "updated_at": func.now()},
    ).returning(
        User.id,
        User.email,
        User.user_type,
        # xmax is 0 only on a freshly inserted row, which tells insert and update apart.
        literal_column("(xmax = 0)").label("created"),
    )
    row = (await session.execute(statement)).one()
    await session.commit()
    return LeadResult(id=row.id, email=row.email, user_type=row.user_type, created=row.created)
