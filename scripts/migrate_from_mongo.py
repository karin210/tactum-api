"""Copy users and reservations from the MongoDB database used by the Next.js app into PostgreSQL.

Safe to re-run: users are matched by email and reservations by their MongoDB id or
Stripe deposit id, so a second run updates rows instead of duplicating them. Run it
once for a rehearsal and again right before switching traffic to the new API.

Usage (PowerShell, from the project root):

    $env:DATABASE_URL = "postgresql://..."   # target, already migrated with `alembic upgrade head`
    $env:MONGODB_URI = "mongodb+srv://..."    # source (MONGODB_EARLYADOPTERS_URI in the Next app)
    uv run --group migration python -m scripts.migrate_from_mongo --dry-run
    uv run --group migration python -m scripts.migrate_from_mongo
"""

import argparse
import asyncio
import logging
import os
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pymongo import MongoClient
from sqlalchemy import case, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import get_engine
from app.models import Reservation, User
from app.models.reservation import PAYMENT_STATUSES, RESERVATION_STATUSES
from app.models.user import USER_TYPES

logger = logging.getLogger("migrate_from_mongo")


async def migrate_users(session: AsyncSession, documents: list[dict[str, Any]]) -> dict[str, UUID]:
    """Upsert every user and return a map from MongoDB id to the new PostgreSQL id."""
    ids: dict[str, UUID] = {}
    for document in documents:
        email = (document.get("email") or "").strip().lower()
        if not email:
            logger.warning("Skipping user %s: no email", document["_id"])
            continue

        values = {
            "email": email,
            "first_name": document.get("first_name"),
            "last_name": document.get("last_name"),
            # Mongoose stored phone as a Number; it's text in PostgreSQL.
            "phone": _phone(document.get("phone")),
            "city": document.get("city") or "Other",
            "user_type": _allowed(document.get("userType"), USER_TYPES, "lead"),
            "stripe_customer_id": document.get("stripeCustomerId"),
            "legacy_mongo_id": str(document["_id"]),
            "created_at": _datetime(document.get("createdAt")),
            "updated_at": _datetime(document.get("updatedAt")),
        }
        statement = insert(User).values(**values)
        user_id = await session.scalar(
            statement.on_conflict_do_update(
                index_elements=[User.email],
                # A user who became a customer in either database stays a customer.
                set_={
                    **{key: statement.excluded[key] for key in values if key != "user_type"},
                    "user_type": case(
                        (
                            or_(
                                User.user_type == "customer",
                                statement.excluded.user_type == "customer",
                            ),
                            "customer",
                        ),
                        else_="lead",
                    ),
                },
            ).returning(User.id)
        )
        ids[str(document["_id"])] = user_id
    return ids


async def migrate_reservations(
    session: AsyncSession, documents: list[dict[str, Any]], user_ids: dict[str, UUID]
) -> int:
    migrated = 0
    for document in documents:
        legacy_id = str(document["_id"])
        user_id = user_ids.get(str(document.get("user")))
        if user_id is None:
            logger.warning("Skipping reservation %s: its user was not migrated", legacy_id)
            continue

        values = {
            "user_id": user_id,
            "status": _allowed(document.get("status"), RESERVATION_STATUSES, "pending"),
            "total_price": _money(document.get("totalPrice")),
            "deposit_amount": _money(document.get("depositAmount")),
            "balance_amount": _money(document.get("balanceAmount")),
            "stripe_customer_id": document.get("stripeCustomerId"),
            "deposit_payment_intent_id": document.get("depositPaymentIntentId"),
            "balance_payment_intent_id": document.get("balancePaymentIntentId"),
            "payment_status": _allowed(document.get("paymentStatus"), PAYMENT_STATUSES, "unpaid"),
            "reservation_date": _datetime(document.get("reservationDate")),
            "city": document.get("city"),
            "extra": document.get("metadata"),
            "legacy_mongo_id": legacy_id,
            "created_at": _datetime(document.get("createdAt")),
            "updated_at": _datetime(document.get("updatedAt")),
        }

        # The new API's webhook may already have written this reservation, so match on
        # either id before deciding between update and insert.
        existing = await session.scalar(
            select(Reservation).where(
                (Reservation.legacy_mongo_id == legacy_id)
                | (
                    (Reservation.deposit_payment_intent_id == values["deposit_payment_intent_id"])
                    & (Reservation.deposit_payment_intent_id.is_not(None))
                )
            )
        )
        if existing:
            for key, value in values.items():
                setattr(existing, key, value)
        else:
            session.add(Reservation(**values))
        migrated += 1
    return migrated


async def main(mongo_uri: str, dry_run: bool) -> None:
    mongo = MongoClient(mongo_uri, serverSelectionTimeoutMS=10_000)
    # Mongoose names collections after the model: User -> users, Reservation -> reservations.
    source = mongo.get_default_database()
    users = list(source["users"].find())
    reservations = list(source["reservations"].find())
    logger.info("Read %d users and %d reservations from MongoDB", len(users), len(reservations))

    async with async_sessionmaker(get_engine(), expire_on_commit=False)() as session:
        user_ids = await migrate_users(session, users)
        migrated = await migrate_reservations(session, reservations, user_ids)
        logger.info("Prepared %d users and %d reservations", len(user_ids), migrated)

        if dry_run:
            await session.rollback()
            logger.info("Dry run: nothing was written")
        else:
            await session.commit()
            logger.info("Committed")

    await get_engine().dispose()
    mongo.close()


def _allowed(value: Any, allowed: tuple[str, ...], default: str) -> str:
    return value if value in allowed else default


def _money(value: Any) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def _phone(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(int(value)) if isinstance(value, float) else str(value)


def _datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        # PyMongo returns naive datetimes that are actually UTC.
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.now(UTC)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mongo-uri", default=os.environ.get("MONGODB_URI"))
    parser.add_argument("--dry-run", action="store_true", help="Run everything, then roll back.")
    args = parser.parse_args()
    if not args.mongo_uri:
        parser.error("Pass --mongo-uri or set MONGODB_URI.")
    asyncio.run(main(args.mongo_uri, args.dry_run))
