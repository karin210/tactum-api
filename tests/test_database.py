"""Integration tests against a real, migrated PostgreSQL database (see TEST_DATABASE_URL)."""

import json
import time
import uuid

from fastapi.testclient import TestClient

from tests.conftest import requires_database, signed_stripe_event

pytestmark = requires_database


def _unique_email() -> str:
    return f"test-{uuid.uuid4().hex[:10]}@example.com"


def _deposit_event(email: str, payment_intent_id: str) -> dict[str, object]:
    return {
        "type": "payment_intent.succeeded",
        "data": {
            "object": {
                "id": payment_intent_id,
                "amount_received": 30000,
                "currency": "mxn",
                "customer": None,
                "receipt_email": None,
                "created": 1_760_000_000,
                "metadata": {
                    "type": "deposit",
                    "email": email,
                    "city": "Guadalajara",
                    "totalPrice": "875",
                    "depositAmount": "300",
                    "reservationDate": "2026-10-20T12:00:00.000Z",
                },
            }
        },
    }


def test_deposit_webhook_creates_customer_and_order_once(live_client: TestClient) -> None:
    email = _unique_email()
    before = live_client.get("/reservations/count").json()["count"]

    payload, signature = signed_stripe_event(_deposit_event(email, f"pi_{uuid.uuid4().hex}"))
    for _ in range(2):  # Stripe can deliver the same event twice.
        response = live_client.post(
            "/webhooks/stripe", content=payload, headers={"Stripe-Signature": signature}
        )
        assert response.status_code == 200

    assert live_client.get("/reservations/count").json()["count"] == before + 1


def test_count_stream_pushes_new_counts(live_client: TestClient) -> None:
    # The listener connects in the background right after startup.
    deadline = time.monotonic() + 5
    while not live_client.app.state.count_broadcaster.is_connected:
        assert time.monotonic() < deadline, "The count listener never connected"
        time.sleep(0.05)

    with live_client.stream("GET", "/reservations/count/stream") as stream:
        assert stream.status_code == 200
        lines = stream.iter_lines()
        first = json.loads(next(line for line in lines if line.startswith("data:"))[5:])

        payload, signature = signed_stripe_event(
            _deposit_event(_unique_email(), f"pi_{uuid.uuid4().hex}")
        )
        live_client.post(
            "/webhooks/stripe", content=payload, headers={"Stripe-Signature": signature}
        )

        second = json.loads(next(line for line in lines if line.startswith("data:"))[5:])
        assert second["count"] == first["count"] + 1
