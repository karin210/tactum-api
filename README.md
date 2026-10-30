# Tactum API

Backend for the [Tactum website](../tactum-website): Stripe deposit payments and
glove orders. FastAPI + [asyncpg](https://magicstack.github.io/asyncpg/) on PostgreSQL (AWS RDS in production).
There is no ORM: queries are plain SQL strings and the schema is plain SQL files.

Moving the backend out of the Next.js app is described step by step in
[docs/MIGRATION.md](docs/MIGRATION.md).

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/payment-intents` | Create (`201`) or update (`200`, with `paymentIntentId`) the deposit PaymentIntent. |
| `POST` | `/webhooks/stripe` | Stripe webhook. Records the order on `payment_intent.succeeded`. |
| `GET` | `/reservations/count` | Current order count. |
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
  db.py              asyncpg pool and the per-request connection dependency
  errors.py          ApiError, request IDs, central error handlers
  dependencies.py    shared FastAPI dependencies (Stripe client, pricing, broadcaster)
  schemas/           Pydantic request/response bodies
  routers/           HTTP endpoints, thin: validate, call a service, shape the response
  services/          business logic and its SQL (payments, orders, live counter)
migrations/          the database schema, as numbered .sql files
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
uv run python -m scripts.migrate
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

The schema lives in `migrations/`, as numbered SQL files (`0002_add_something.sql`). Add a new
file for each change and never edit one that has already been applied:

```powershell
uv run python -m scripts.migrate
```

The runner applies the files that haven't run yet, each in its own transaction, and records them
in the `schema_migrations` table. Migrations only move forward; to undo one, write a new file.
The live counter depends on the `orders_changed` trigger from the `0002` migration.

`updated_at` is not touched automatically: every `UPDATE` and `ON CONFLICT DO UPDATE` must set
`updated_at = now()` itself.

If the database was created earlier with Alembic, mark the first migration as already applied
instead of running it, then drop the old bookkeeping table:

```sql
CREATE TABLE schema_migrations (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now());
INSERT INTO schema_migrations (version) VALUES ('0001_create_users_and_reservations');
DROP TABLE alembic_version;
```
