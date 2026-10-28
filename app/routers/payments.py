from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.dependencies import get_deposit_payments
from app.schemas.payments import PaymentIntentIn, PaymentIntentOut
from app.services.payments import DepositPayments

router = APIRouter(prefix="/payment-intents", tags=["payments"])


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses={200: {"model": PaymentIntentOut, "description": "The existing intent was updated."}},
)
async def create_payment_intent(
    body: PaymentIntentIn,
    response: Response,
    payments: Annotated[DepositPayments, Depends(get_deposit_payments)],
) -> PaymentIntentOut:
    """Create the deposit PaymentIntent, or update it when `paymentIntentId` is sent."""
    intent, created = await payments.create_or_update(body)
    if not created:
        response.status_code = status.HTTP_200_OK
    return PaymentIntentOut(client_secret=intent.client_secret, payment_intent_id=intent.id)
