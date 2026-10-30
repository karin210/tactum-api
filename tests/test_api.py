"""Tests that don't need a database or network access."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_deposit_payments
from app.main import app
from app.schemas.payments import PaymentIntentIn
from app.services.pricing import DepositPricing
from tests.conftest import signed_stripe_event


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["X-Request-ID"]


def test_unknown_route_returns_consistent_404(client: TestClient) -> None:
    response = client.get("/nope")
    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "http_error"
    assert body["requestId"] == response.headers["X-Request-ID"]


def test_payment_intent_rejects_client_supplied_amounts(client: TestClient) -> None:
    response = client.post("/payment-intents", json={"email": "ana@example.com", "totalPrice": 1})
    assert response.status_code == 422


class FakeDepositPayments:
    def __init__(self) -> None:
        self.requests: list[PaymentIntentIn] = []

    async def create_or_update(self, request: PaymentIntentIn) -> tuple[SimpleNamespace, bool]:
        self.requests.append(request)
        intent = SimpleNamespace(id="pi_123", client_secret="pi_123_secret_abc")
        return intent, request.payment_intent_id is None


def test_payment_intent_create_then_update(client: TestClient) -> None:
    fake = FakeDepositPayments()
    app.dependency_overrides[get_deposit_payments] = lambda: fake

    created = client.post("/payment-intents", json={"email": " Ana@Example.com ", "city": ""})
    assert created.status_code == 201
    assert created.json() == {"clientSecret": "pi_123_secret_abc", "paymentIntentId": "pi_123"}
    assert fake.requests[0].email == "ana@example.com"
    assert fake.requests[0].city is None

    updated = client.post("/payment-intents", json={"paymentIntentId": "pi_123", "name": "Ana"})
    assert updated.status_code == 200


def test_webhook_rejects_missing_or_bad_signature(client: TestClient) -> None:
    assert client.post("/webhooks/stripe", content=b"{}").status_code == 400

    payload, _ = signed_stripe_event({"type": "ping"})
    response = client.post(
        "/webhooks/stripe", content=payload, headers={"Stripe-Signature": "t=1,v1=bad"}
    )
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_signature"


def test_webhook_acknowledges_events_it_ignores(client: TestClient) -> None:
    payload, signature = signed_stripe_event({"type": "customer.created", "data": {"object": {}}})
    response = client.post(
        "/webhooks/stripe", content=payload, headers={"Stripe-Signature": signature}
    )
    assert response.status_code == 200
    assert response.json() == {"received": True}


def test_pricing() -> None:
    pricing = DepositPricing(total_price_mxn=875, deposit_mxn=300)
    assert pricing.balance_mxn == 575
    assert pricing.deposit_minor_units == 30000
    metadata = pricing.metadata(email=None, city="CDMX")
    assert metadata["type"] == "deposit"
    assert metadata["email"] == "pending"
    assert metadata["totalPrice"] == "875"


def test_pricing_rejects_a_deposit_larger_than_the_total() -> None:
    with pytest.raises(ValueError):
        DepositPricing(total_price_mxn=100, deposit_mxn=300)
