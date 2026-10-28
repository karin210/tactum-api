import logging
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Reservation, User

logger = logging.getLogger(__name__)


async def count_reservations(session: AsyncSession) -> int:
    return await session.scalar(select(func.count()).select_from(Reservation)) or 0


async def record_deposit(session: AsyncSession, payment_intent: dict[str, Any]) -> None:
    """Turn a succeeded deposit PaymentIntent into a customer and a confirmed reservation.

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

    async with session.begin():
        user_statement = insert(User).values(email=email, user_type="customer")
        user_id = await session.scalar(
            user_statement.on_conflict_do_update(
                index_elements=[User.email],
                set_={"user_type": "customer", "updated_at": func.now()},
            ).returning(User.id)
        )

        values = {
            "user_id": user_id,
            "status": "confirmed",
            "total_price": total_price,
            "deposit_amount": deposit_amount,
            "balance_amount": total_price - deposit_amount,
            "currency": payment_intent["currency"].upper(),
            "stripe_customer_id": payment_intent.get("customer"),
            "deposit_payment_intent_id": payment_intent["id"],
            "payment_status": "deposit_paid",
            "reservation_date": _reservation_date(payment_intent, metadata),
            "city": metadata.get("city"),
        }
        reservation_statement = insert(Reservation).values(**values)
        await session.execute(
            reservation_statement.on_conflict_do_update(
                index_elements=[Reservation.deposit_payment_intent_id],
                set_={**values, "updated_at": func.now()},
            )
        )

    logger.info("Reservation confirmed for deposit %s", payment_intent["id"])


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


def _reservation_date(payment_intent: dict[str, Any], metadata: dict[str, str]) -> datetime:
    try:
        return datetime.fromisoformat(metadata["reservationDate"])
    except KeyError, ValueError:
        return datetime.fromtimestamp(payment_intent["created"], tz=UTC)
