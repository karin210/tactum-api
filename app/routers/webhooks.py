import json
from typing import Annotated

import asyncpg
import stripe
from fastapi import APIRouter, Depends, Header, Request

from app.config import Settings, get_settings
from app.db import get_connection
from app.errors import ApiError
from app.schemas.payments import WebhookAck
from app.services.reservations import record_deposit

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/stripe", include_in_schema=False)
async def stripe_webhook(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    connection: Annotated[asyncpg.Connection, Depends(get_connection)],
    stripe_signature: Annotated[str | None, Header()] = None,
) -> WebhookAck:
    # The signature is computed over the exact bytes Stripe sent, so read the raw body.
    payload = await request.body()
    if not stripe_signature:
        raise ApiError(400, "missing_signature", "Missing Stripe signature.")
    try:
        stripe.Webhook.construct_event(payload, stripe_signature, settings.stripe_webhook_secret)
    except (ValueError, stripe.SignatureVerificationError) as exc:
        raise ApiError(400, "invalid_signature", "Invalid Stripe signature.") from exc

    # Verified; read it as plain JSON so the code doesn't depend on the SDK's object model.
    event = json.loads(payload)
    if event["type"] == "payment_intent.succeeded":
        # A database failure here becomes a 500, which makes Stripe retry the delivery.
        await record_deposit(connection, event["data"]["object"])

    return WebhookAck()
