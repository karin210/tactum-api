import logging

import stripe

from app.errors import ApiError
from app.schemas.payments import PaymentIntentIn
from app.services.pricing import DepositPricing

logger = logging.getLogger(__name__)

# Once an intent reaches any other status it can no longer be edited.
UPDATABLE_STATUSES = {"requires_payment_method", "requires_confirmation", "requires_action"}


class DepositPayments:
    """Creates and updates the Stripe PaymentIntent used to pay an order deposit."""

    def __init__(self, client: stripe.StripeClient, pricing: DepositPricing) -> None:
        self._client = client
        self._pricing = pricing

    async def create_or_update(self, request: PaymentIntentIn) -> tuple[stripe.PaymentIntent, bool]:
        """Return the intent and whether it was newly created."""
        params = {
            "amount": self._pricing.deposit_minor_units,
            "currency": self._pricing.currency,
            "metadata": self._pricing.metadata(email=request.email, city=request.city),
        }
        if request.email:
            params["receipt_email"] = request.email

        try:
            if request.payment_intent_id:
                await self._ensure_updatable(request.payment_intent_id)
                intent = await self._client.v1.payment_intents.update_async(
                    request.payment_intent_id, params
                )
                return intent, False

            intent = await self._client.v1.payment_intents.create_async(
                {**params, "automatic_payment_methods": {"enabled": True}}
            )
            return intent, True
        except stripe.InvalidRequestError as exc:
            logger.warning("Stripe rejected the payment intent request: %s", exc.user_message)
            raise ApiError(
                400,
                "payment_intent_invalid",
                "This payment can't be updated. Please reload the page.",
            ) from exc
        except stripe.StripeError as exc:
            logger.exception("Stripe request failed")
            raise ApiError(
                502,
                "payment_provider_unavailable",
                "The payment service is not available right now. Please try again in a moment.",
            ) from exc

    async def _ensure_updatable(self, payment_intent_id: str) -> None:
        """Only deposit intents still waiting for payment can be changed from the browser."""
        intent = await self._client.v1.payment_intents.retrieve_async(payment_intent_id)
        if (
            intent.metadata.to_dict().get("type") != "deposit"
            or intent.status not in UPDATABLE_STATUSES
        ):
            raise ApiError(
                409,
                "payment_intent_locked",
                "This payment can't be changed anymore. Please reload the page.",
            )
