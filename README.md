# Tactum API

Backend for the [Tactum website](../tactum-website): newsletter leads, Stripe deposit payments and
glove reservations. FastAPI + SQLAlchemy 2 (async) on PostgreSQL (AWS RDS in production).

Moving the backend out of the Next.js app is described step by step in
[docs/MIGRATION.md](docs/MIGRATION.md).

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/leads` | Subscribe to news. `201` new lead, `200` existing email updated. |
| `POST` | `/payment-intents` | Create (`201`) or update (`200`, with `paymentIntentId`) the deposit PaymentIntent. |
| `POST` | `/webhooks/stripe` | Stripe webhook. Records the reservation on `payment_intent.succeeded`. |
| `GET` | `/reservations/count` | Current reservation count. |
| `GET` | `/reservations/count/stream` | Server-Sent Events: `data: {"count": n}` on connect and on every change. |
| `GET` | `/health`, `/health/db` | Liveness, and readiness (the database is reachable). |

JSON bodies use camelCase. Errors always have the shape
`{ "code", "message", "requestId", "fieldErrors"? }`. `code` is stable, so the frontend can map it
to a translated message. Interactive docs are at `/docs` when `ENVIRONMENT=development`.

## Project layout

```
app/
  main.py            app factory, middleware, lifespan (starts the live counter listener)
  config.py          settings from environment variables / .env
  db.py              async engine and session dependency
  errors.py          ApiError, request IDs, central error handlers
  dependencies.py    shared FastAPI dependencies (Stripe client, pricing, broadcaster)
  models/            SQLAlchemy tables
  schemas/           Pydantic request/response bodies
  routers/           HTTP endpoints, thin: validate, call a service, shape the response
  services/          business logic (leads, payments, reservations, live counter)
alembic/             database migrations
scripts/             one-off tasks (copying data from MongoDB)
tests/               unit tests, plus integration tests when TEST_DATABASE_URL is set
```

## Local development (PowerShell)

Python and uv are pinned in `mise.toml`.

```powershell
mise install
uv sync
Copy-Item .env.example .env   # then fill in the values
```

You need a PostgreSQL 17 database. The simplest option on Windows is
`winget install PostgreSQL.PostgreSQL.17`. Then create the database and apply the migrations:

```powershell
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

To receive Stripe webhooks locally, forward them with the Stripe CLI and put the `whsec_...` it
prints into `.env`:

```powershell
stripe listen --forward-to localhost:8000/webhooks/stripe
```

## Tests and linting

```powershell
uv run pytest                     # unit tests only
$env:TEST_DATABASE_URL = 'postgresql://postgres:postgres@localhost:5432/tactum_test'
uv run pytest                     # also runs the integration tests (database must be migrated)
uv run ruff check . ; uv run ruff format --check .
```

## Database changes

Edit the models in `app/models/`, then generate and review a migration:

```powershell
uv run alembic revision --autogenerate -m "describe the change"
uv run alembic upgrade head
```

Autogenerate does not see triggers or functions. The live counter depends on the
`reservations_changed` trigger created in the first migration.
