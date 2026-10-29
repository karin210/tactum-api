"""Copy users and reservations from the MongoDB database used by the Next.js app into PostgreSQL.

Safe to re-run: users are matched by email and reservations by their MongoDB id or
Stripe deposit id, so a second run updates rows instead of duplicating them. Run it
once for a rehearsal and again right before switching traffic to the new API.

Usage (PowerShell, from the project root):

    $env:DATABASE_URL = "postgresql://..."   # target, already run through scripts.migrate
    $env:MONGODB_URI = "mongodb+srv://..."    # source (MONGODB_EARLYADOPTERS_URI in the Next app)
    uv run --group migration python -m scripts.migrate_from_mongo --dry-run
    uv run --group migration python -m scripts.migrate_from_mongo
"""

import argparse
import asyncio
import json
import logging
import os
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import asyncpg
from pymongo import MongoClient

from app.config import get_database_settings
from app.db import ssl_option

logger = logging.getLogger("migrate_from_mongo")

USER_TYPES = ("lead", "customer")
RESERVATION_STATUSES = ("pending", "confirmed", "completed", "cancelled")
PAYMENT_STATUSES = ("unpaid", "deposit_paid", "fully_paid", "refunded")

# A user who became a customer in either database stays a customer.
UPSERT_USER = """
    INSERT INTO users (
        email, first_name, last_name, phone, city, user_type,
        stripe_customer_id, legacy_mongo_id, created_at, updated_at
    )
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
    ON CONFLICT (email) DO UPDATE SET
        first_name = EXCLUDED.first_name,
        last_name = EXCLUDED.last_name,
        phone = EXCLUDED.phone,
        city = EXCLUDED.city,
        stripe_customer_id = EXCLUDED.stripe_customer_id,
        legacy_mongo_id = EXCLUDED.legacy_mongo_id,
        created_at = EXCLUDED.created_at,
        updated_at = EXCLUDED.updated_at,
        user_type = CASE
            WHEN users.user_type = 'customer' OR EXCLUDED.user_type = 'customer' THEN 'customer'
            ELSE 'lead'
        END
    RETURNING id
"""

# The new API's webhook may already have written a reservation, so match on either id.
# A NULL deposit id never equals anything, so it can't match by accident.
FIND_RESERVATION = """
    SELECT id FROM reservations
    WHERE legacy_mongo_id = $1 OR deposit_payment_intent_id = $2
    LIMIT 1
"""

INSERT_RESERVATION = """
    INSERT INTO reservations (
        user_id, status, total_price, deposit_amount, balance_amount,
        stripe_customer_id, deposit_payment_intent_id, balance_payment_intent_id,
        payment_status, reservation_date, city, metadata, legacy_mongo_id,
        created_at, updated_at
    )
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::jsonb, $13, $14, $15)
"""

UPDATE_RESERVATION = """
    UPDATE reservations SET
        user_id = $1, status = $2, total_price = $3, deposit_amount = $4, balance_amount = $5,
        stripe_customer_id = $6, deposit_payment_intent_id = $7, balance_payment_intent_id = $8,
        payment_status = $9, reservation_date = $10, city = $11, metadata = $12::jsonb,
        legacy_mongo_id = $13, created_at = $14, updated_at = $15
    WHERE id = $16
"""


async def migrate_users(
    connection: asyncpg.Connection, documents: list[dict[str, Any]]
) -> dict[str, UUID]:
    """Upsert every user and return a map from MongoDB id to the new PostgreSQL id."""
    ids: dict[str, UUID] = {}
    for document in documents:
        email = (document.get("email") or "").strip().lower()
        if not email:
            logger.warning("Skipping user %s: no email", document["_id"])
            continue

        user_id = await connection.fetchval(
            UPSERT_USER,
            email,
            document.get("first_name"),
            document.get("last_name"),
            # Mongoose stored phone as a Number; it's text in PostgreSQL.
            _phone(document.get("phone")),
            document.get("city") or "Other",
            _allowed(document.get("userType"), USER_TYPES, "lead"),
            document.get("stripeCustomerId"),
            str(document["_id"]),
            _datetime(document.get("createdAt")),
            _datetime(document.get("updatedAt")),
        )
        ids[str(document["_id"])] = user_id
    return ids


async def migrate_reservations(
    connection: asyncpg.Connection, documents: list[dict[str, Any]], user_ids: dict[str, UUID]
) -> int:
    migrated = 0
    for document in documents:
        legacy_id = str(document["_id"])
        user_id = user_ids.get(str(document.get("user")))
        if user_id is None:
            logger.warning("Skipping reservation %s: its user was not migrated", legacy_id)
            continue

        deposit_payment_intent_id = document.get("depositPaymentIntentId")
        values = (
            user_id,
            _allowed(document.get("status"), RESERVATION_STATUSES, "pending"),
            _money(document.get("totalPrice")),
            _money(document.get("depositAmount")),
            _money(document.get("balanceAmount")),
            document.get("stripeCustomerId"),
            deposit_payment_intent_id,
            document.get("balancePaymentIntentId"),
            _allowed(document.get("paymentStatus"), PAYMENT_STATUSES, "unpaid"),
            _datetime(document.get("reservationDate")),
            document.get("city"),
            json.dumps(document.get("metadata"), default=str),
            legacy_id,
            _datetime(document.get("createdAt")),
            _datetime(document.get("updatedAt")),
        )

        existing_id = await connection.fetchval(
            FIND_RESERVATION, legacy_id, deposit_payment_intent_id
        )
        if existing_id:
            await connection.execute(UPDATE_RESERVATION, *values, existing_id)
        else:
            await connection.execute(INSERT_RESERVATION, *values)
        migrated += 1
    return migrated


async def main(mongo_uri: str, dry_run: bool) -> None:
    mongo = MongoClient(mongo_uri, serverSelectionTimeoutMS=10_000)
    # Mongoose names collections after the model: User -> users, Reservation -> reservations.
    source = mongo.get_default_database()
    users = list(source["users"].find())
    reservations = list(source["reservations"].find())
    logger.info("Read %d users and %d reservations from MongoDB", len(users), len(reservations))

    settings = get_database_settings()
    connection = await asyncpg.connect(settings.database_url, ssl=ssl_option(settings))
    try:
        transaction = connection.transaction()
        await transaction.start()
        try:
            user_ids = await migrate_users(connection, users)
            migrated = await migrate_reservations(connection, reservations, user_ids)
            logger.info("Prepared %d users and %d reservations", len(user_ids), migrated)
        except BaseException:
            await transaction.rollback()
            raise

        if dry_run:
            await transaction.rollback()
            logger.info("Dry run: nothing was written")
        else:
            await transaction.commit()
            logger.info("Committed")
    finally:
        await connection.close()
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
