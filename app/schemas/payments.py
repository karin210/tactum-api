from pydantic import Field

from app.schemas.base import ApiModel, OptionalEmail, OptionalString, RequestModel


class PaymentIntentIn(RequestModel):
    """Contact details for a deposit. Amounts are deliberately absent: the server sets them."""

    # Present when the client is updating the intent it created earlier.
    payment_intent_id: str | None = Field(
        default=None, pattern=r"^pi_[A-Za-z0-9]+$", max_length=255
    )
    email: OptionalEmail = None
    name: OptionalString = Field(default=None, max_length=200)
    phone: OptionalString = Field(default=None, max_length=30)
    city: OptionalString = Field(default=None, max_length=100)


class PaymentIntentOut(ApiModel):
    client_secret: str
    payment_intent_id: str


class WebhookAck(ApiModel):
    received: bool = True
