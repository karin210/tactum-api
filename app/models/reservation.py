import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

RESERVATION_STATUSES = ("pending", "confirmed", "completed", "cancelled")
PAYMENT_STATUSES = ("unpaid", "deposit_paid", "fully_paid", "refunded")


class Reservation(TimestampMixin, Base):
    """A glove reservation, created once Stripe confirms the deposit payment."""

    __tablename__ = "reservations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'confirmed', 'completed', 'cancelled')", name="status"
        ),
        CheckConstraint(
            "payment_status IN ('unpaid', 'deposit_paid', 'fully_paid', 'refunded')",
            name="payment_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[str] = mapped_column(String(20), server_default="pending")

    # Amounts in MXN (not cents), with two decimals.
    total_price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    deposit_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    balance_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(3), server_default="MXN")

    stripe_customer_id: Mapped[str | None] = mapped_column(String(255))
    # Unique so a webhook delivered twice updates the same reservation instead of duplicating it.
    deposit_payment_intent_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    balance_payment_intent_id: Mapped[str | None] = mapped_column(String(255))
    payment_status: Mapped[str] = mapped_column(String(20), server_default="unpaid")

    reservation_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    city: Mapped[str | None] = mapped_column(String(100))
    # "metadata" is reserved by SQLAlchemy, so the attribute is named differently from the column.
    extra: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB)
    legacy_mongo_id: Mapped[str | None] = mapped_column(String(24), unique=True)
