from functools import lru_cache

import stripe
from fastapi import Request

from app.config import get_settings
from app.services.count_broadcaster import ReservationCountBroadcaster
from app.services.payments import DepositPayments
from app.services.pricing import DepositPricing


@lru_cache
def get_pricing() -> DepositPricing:
    settings = get_settings()
    return DepositPricing(
        total_price_mxn=settings.product_total_price_mxn,
        deposit_mxn=settings.deposit_amount_mxn,
    )


@lru_cache
def get_stripe_client() -> stripe.StripeClient:
    return stripe.StripeClient(get_settings().stripe_secret_key, http_client=stripe.HTTPXClient())


def get_deposit_payments() -> DepositPayments:
    return DepositPayments(get_stripe_client(), get_pricing())


def get_count_broadcaster(request: Request) -> ReservationCountBroadcaster:
    return request.app.state.count_broadcaster
