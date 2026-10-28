import hashlib
import hmac
import json
import os
import time
from collections.abc import Iterator

import pytest

# Settings are read when the app is imported, so they must exist before any app import.
# Integration tests use TEST_DATABASE_URL; unit tests never open a connection.
os.environ.setdefault(
    "DATABASE_URL",
    os.environ.get("TEST_DATABASE_URL", "postgresql://test:test@localhost:5432/test"),
)
os.environ.setdefault("STRIPE_SECRET_KEY", "sk_test_dummy")
os.environ.setdefault("STRIPE_WEBHOOK_SECRET", "whsec_test_dummy")
os.environ.setdefault("ENVIRONMENT", "development")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

requires_database = pytest.mark.skipif(
    "TEST_DATABASE_URL" not in os.environ,
    reason="Set TEST_DATABASE_URL to a migrated PostgreSQL database to run integration tests.",
)


@pytest.fixture
def client() -> Iterator[TestClient]:
    """A client without the lifespan, so no database connection is opened."""
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def live_client() -> Iterator[TestClient]:
    """A client with the lifespan running, for tests against a real database."""
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def signed_stripe_event(event: dict[str, object]) -> tuple[bytes, str]:
    """Build a webhook body and the Stripe-Signature header Stripe would send for it."""
    payload = json.dumps(event).encode()
    timestamp = int(time.time())
    secret = os.environ["STRIPE_WEBHOOK_SECRET"].encode()
    signature = hmac.new(secret, f"{timestamp}.".encode() + payload, hashlib.sha256).hexdigest()
    return payload, f"t={timestamp},v1={signature}"
