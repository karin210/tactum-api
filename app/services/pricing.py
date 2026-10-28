from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class DepositPricing:
    """The price of a glove reservation and the deposit charged up front, in whole MXN."""

    total_price_mxn: int
    deposit_mxn: int
    currency: str = "mxn"

    def __post_init__(self) -> None:
        if not 0 < self.deposit_mxn <= self.total_price_mxn:
            raise ValueError("The deposit must be positive and no larger than the total price.")

    @property
    def balance_mxn(self) -> int:
        return self.total_price_mxn - self.deposit_mxn

    @property
    def deposit_minor_units(self) -> int:
        """Stripe amounts are in cents."""
        return self.deposit_mxn * 100

    def metadata(self, *, email: str | None, city: str | None) -> dict[str, str]:
        """Metadata stored on the PaymentIntent and read back by the webhook.

        The keys match the ones the Next.js route used, so intents created before the
        cutover are still processed correctly by the new webhook.
        """
        return {
            "type": "deposit",
            "email": email or "pending",
            "city": city or "pending",
            "totalPrice": str(self.total_price_mxn),
            "depositAmount": str(self.deposit_mxn),
            "reservationDate": datetime.now(UTC).isoformat(),
        }
