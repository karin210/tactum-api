import logging
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

# A payer we've never seen is created as a user; a known one is reused.
UPSERT_CUSTOMER = """
    INSERT INTO users (email)
    VALUES ($1)
    ON CONFLICT (email) DO UPDATE SET updated_at = now()
    RETURNING id
"""

# Keyed on the unique deposit_payment_intent_id, so a webhook delivered twice updates the
# same order instead of adding a second one.
UPSERT_DEPOSIT_ORDER = """
    INSERT INTO orders (
        user_id, status, total_price, deposit_amount, balance_amount, currency,
        stripe_customer_id, deposit_payment_intent_id, payment_status, order_date, city
    )
    VALUES ($1, 'confirmed', $2, $3, $4, $5, $6, $7, 'deposit_paid', $8, $9)
    ON CONFLICT (deposit_payment_intent_id) DO UPDATE SET
        user_id = EXCLUDED.user_id,
        status = EXCLUDED.status,
        total_price = EXCLUDED.total_price,
        deposit_amount = EXCLUDED.deposit_amount,
        balance_amount = EXCLUDED.balance_amount,
        currency = EXCLUDED.currency,
        stripe_customer_id = EXCLUDED.stripe_customer_id,
        payment_status = EXCLUDED.payment_status,
        order_date = EXCLUDED.order_date,
        city = EXCLUDED.city,
        updated_at = now()
"""


async def count_orders(connection: asyncpg.Connection) -> int:
    return await connection.fetchval("SELECT count(*) FROM orders")


async def record_deposit(connection: asyncpg.Connection, payment_intent: dict[str, Any]) -> None:
    """Turn a succeeded deposit PaymentIntent into a customer and a confirmed order.

    Safe to run more than once for the same intent: Stripe may deliver a webhook twice,
    and both writes are upserts keyed on unique columns.
    """
    metadata: dict[str, str] = payment_intent.get("metadata") or {}
    if metadata.get("type") != "deposit":
        return

    email = _customer_email(payment_intent, metadata)
    if email is None:
        # Retrying won't fix a missing email, so log it loudly for manual follow-up.
        logger.error("Deposit %s succeeded without a customer email", payment_intent["id"])
        return

    total_price = _decimal(metadata.get("totalPrice"))
    # What Stripe actually collected, not what the metadata claims.
    deposit_amount = Decimal(payment_intent["amount_received"]) / 100
    if total_price is None:
        logger.error("Deposit %s has no valid totalPrice in its metadata", payment_intent["id"])
        return

    # Both writes commit together, or neither does.
    async with connection.transaction():
        user_id = await connection.fetchval(UPSERT_CUSTOMER, email)
        await connection.execute(
            UPSERT_DEPOSIT_ORDER,
            user_id,
            total_price,
            deposit_amount,
            total_price - deposit_amount,
            payment_intent["currency"].upper(),
            payment_intent.get("customer"),
            payment_intent["id"],
            _order_date(payment_intent, metadata),
            metadata.get("city"),
        )

    logger.info("Order confirmed for deposit %s", payment_intent["id"])


def _customer_email(payment_intent: dict[str, Any], metadata: dict[str, str]) -> str | None:
    for candidate in (metadata.get("email"), payment_intent.get("receipt_email")):
        if candidate and "@" in candidate:
            return candidate.strip().lower()
    return None


def _decimal(value: str | None) -> Decimal | None:
    try:
        return Decimal(value) if value else None
    except InvalidOperation:
        return None


def _order_date(payment_intent: dict[str, Any], metadata: dict[str, str]) -> datetime:
    try:
        # The key is part of the Stripe metadata contract, so it keeps its old name.
        return datetime.fromisoformat(metadata["reservationDate"])
    except KeyError, ValueError:
        return datetime.fromtimestamp(payment_intent["created"], tz=UTC)
