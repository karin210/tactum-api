import uuid

from sqlalchemy import CheckConstraint, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

USER_TYPES = ("lead", "customer")


class User(TimestampMixin, Base):
    """A lead from the newsletter form or a customer who paid a deposit."""

    __tablename__ = "users"
    __table_args__ = (CheckConstraint("user_type IN ('lead', 'customer')", name="user_type"),)

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    # Always stored trimmed and lowercased, so a plain unique constraint is enough.
    email: Mapped[str] = mapped_column(String(254), unique=True)
    # Customers created by the Stripe webhook may arrive with only an email.
    first_name: Mapped[str | None] = mapped_column(String(100))
    last_name: Mapped[str | None] = mapped_column(String(100))
    phone: Mapped[str | None] = mapped_column(String(30))
    city: Mapped[str] = mapped_column(String(100), server_default="Other")
    user_type: Mapped[str] = mapped_column(String(20), server_default="lead")
    stripe_customer_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    # The MongoDB ObjectId this row was copied from; lets the data migration be re-run safely.
    legacy_mongo_id: Mapped[str | None] = mapped_column(String(24), unique=True)
