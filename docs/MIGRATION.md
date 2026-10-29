# Migrating the Tactum backend from Next.js + MongoDB to FastAPI + AWS RDS PostgreSQL

This guide moves the backend out of `tactum-website` into this project, and the data from MongoDB
Atlas into PostgreSQL on AWS RDS, without downtime and with a way back at every step.

## Contents

1. [What changes](#1-what-changes)
2. [What the EC2 instance changes](#2-what-the-ec2-instance-changes)
3. [Create the database (RDS)](#3-create-the-database-rds)
4. [Create the API server (EC2)](#4-create-the-api-server-ec2)
5. [Deploy the API](#5-deploy-the-api)
6. [Copy the data from MongoDB (rehearsal)](#6-copy-the-data-from-mongodb-rehearsal)
7. [Point Stripe at the new webhook](#7-point-stripe-at-the-new-webhook)
8. [Update the Next.js app](#8-update-the-nextjs-app)
9. [Cutover](#9-cutover)
10. [Clean up](#10-clean-up)
11. [Rollback](#11-rollback)
12. [Behaviour changes to know about](#12-behaviour-changes-to-know-about)
13. [Follow-ups](#13-follow-ups)

---

## 1. What changes

| Today, in `tactum-website` | After, in `tactum-api` |
|---|---|
| `src/server/actions/users.ts` (`createUser`, Mongoose upsert) | `POST /leads`. The server action stays, but calls the API. |
| `src/app/api/payment-intent/route.ts` | `POST /payment-intents` |
| `src/app/api/webhooks/stripe/route.ts` | `POST /webhooks/stripe` |
| `src/app/api/reservations/count/stream/route.ts` (MongoDB change stream) | `GET /reservations/count/stream` (PostgreSQL `LISTEN/NOTIFY`) |
| `src/server/models/*`, `mongoDBConnection.ts` | SQL files in `migrations/`, queries in `app/services/` |
| MongoDB Atlas | AWS RDS for PostgreSQL 17 |

What stays in Next.js: every page, the translations (`next-intl`) and Stripe Elements in the
browser. The API returns stable error codes, and Next.js turns them into translated text, so the
backend never needs to know the user's language.

Target architecture:

```
Browser ──► Vercel (Next.js) ──server action──┐
   │                                          ▼
   └── fetch / EventSource ──► api.tactum.mx (EC2: Caddy ► FastAPI container)
                                              │ private network, TLS
Stripe ── webhook ────────────────────────────┤
                                              ▼
                                   RDS PostgreSQL (private subnets)
```

---

## 2. What the EC2 instance changes

RDS is a *managed* database. AWS runs the server, patches it, backs it up and fails it over. You
don't need an EC2 instance to run or "manage" an RDS database. An EC2 instance is still useful
here, in two roles:

1. **Hosting the API.** Recommended, and what this guide does. The FastAPI container runs on
   the instance, in the same VPC as the database. That gives you three things:
   - The database stays private. It has no public IP, and only the API server's security group
     can open port 5432.
   - The live counter needs a long-lived connection (Server-Sent Events plus a `LISTEN`
     connection to PostgreSQL). A normal server handles that well. Serverless options like
     Lambda handle it poorly.
2. **Your door into the database.** Because RDS is private, you reach it *through* the instance
   for migrations, the data copy, or opening it in a SQL client, using an SSM Session Manager
   tunnel (step 4.4). No SSH keys and no open SSH port.

What it affects in practice:

- **Same region and VPC.** Put the instance and the database in the same AWS region and VPC,
  ideally the same Availability Zone, for the lowest latency.
- **Security groups.** The database allows port 5432 only from the API server's security group,
  not from IP addresses.
- **Instance architecture.** Graviton instances (`t4g`) are ARM. Build the Docker image on the
  instance itself (step 5), or build for `linux/arm64` if you build elsewhere.
- **Atlas allow-list.** To run the data copy from the instance, MongoDB Atlas must allow the
  instance's public (Elastic) IP.

> **If you meant installing PostgreSQL yourself on the EC2 instance instead of using RDS:** the
> code works the same, since only `DATABASE_URL` changes. But you take on backups,
> point-in-time recovery, upgrades, disk monitoring and failover yourself. For a store taking
> payments, RDS is worth it. The rest of this guide assumes RDS.

---

## 3. Create the database (RDS)

Do this in the AWS console. Pick one region for everything, e.g. **Mexico (Central)
`mx-central-1`** if most customers are in Mexico, or `us-east-1`.

### 3.1 Networking

1. Use the default VPC or your own. You need **private subnets in at least two Availability
   Zones**. RDS requires a subnet group spanning two zones, even for a single instance.
2. Create two security groups:
   - `tactum-api-sg` (for EC2): inbound `80` and `443` from `0.0.0.0/0`. **No port 22.**
   - `tactum-db-sg` (for RDS): inbound `5432` **from the security group `tactum-api-sg`**
     (choose the group as the source, not an IP).
3. RDS → *Subnet groups* → create `tactum-db-subnets` with the private subnets.

### 3.2 The instance

RDS → *Create database* → *Standard create*:

| Setting | Value |
|---|---|
| Engine | PostgreSQL 17 (latest minor) |
| Template | Production, or Free tier for staging |
| Identifier | `tactum-db` |
| Master username | `tactum_admin` |
| Credentials | **Managed in AWS Secrets Manager** |
| Instance class | `db.t4g.micro` to start (scale later without data loss) |
| Storage | gp3, 20 GiB, storage autoscaling on |
| Multi-AZ | Off to start. Turn it on when downtime during maintenance matters. |
| Public access | **No** |
| VPC security group | `tactum-db-sg` only |
| Subnet group | `tactum-db-subnets` |
| Initial database name | `tactum` |
| Automated backups | 7 days or more |
| Encryption | On (default) |
| Deletion protection | **On** |

PostgreSQL 15+ on RDS requires TLS by default (`rds.force_ssl = 1`), and the API is configured
for it (step 5.2).

### 3.3 Database roles

Don't let the app connect as the master user. Once you can reach the database (step 4.4), run
this as `tactum_admin` in the `tactum` database:

```sql
-- Owns the tables; used only to run migrations.
CREATE ROLE tactum_owner LOGIN PASSWORD '<strong password 1>';
GRANT CONNECT, CREATE ON DATABASE tactum TO tactum_owner;
GRANT ALL ON SCHEMA public TO tactum_owner;

-- Used by the running API: read and write rows, nothing else.
CREATE ROLE tactum_app LOGIN PASSWORD '<strong password 2>';
GRANT CONNECT ON DATABASE tactum TO tactum_app;
GRANT USAGE ON SCHEMA public TO tactum_app;
ALTER DEFAULT PRIVILEGES FOR ROLE tactum_owner IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO tactum_app;
```

Store both passwords in AWS Secrets Manager or SSM Parameter Store, not in the repository.

---

## 4. Create the API server (EC2)

### 4.1 IAM role

IAM → *Roles* → create `tactum-api-ec2` for EC2 with the managed policy
**`AmazonSSMManagedInstanceCore`**. This is what makes Session Manager (shell and tunnels) work
without SSH.

### 4.2 The instance

EC2 → *Launch instance*:

| Setting | Value |
|---|---|
| AMI | Amazon Linux 2023 (arm64) |
| Type | `t4g.small` |
| Key pair | None (access is through Session Manager) |
| Subnet | A **public** subnet in the same VPC as the database |
| Security group | `tactum-api-sg` |
| IAM instance profile | `tactum-api-ec2` |
| Storage | 20 GiB gp3 |

Then allocate an **Elastic IP** and associate it with the instance, so the IP survives restarts.
Point a DNS record at it: `api.tactum.mx  A  <elastic ip>`.

### 4.3 Install Docker, Git and Caddy

Connect with EC2 → *Connect* → *Session Manager*, then:

```bash
sudo dnf install -y docker git postgresql17
sudo systemctl enable --now docker
sudo usermod -aG docker ssm-user

# Caddy: reverse proxy with automatic HTTPS certificates. It also streams SSE without buffering.
sudo dnf install -y 'dnf-command(copr)'
sudo dnf copr enable -y @caddy/caddy
sudo dnf install -y caddy
```

If the Caddy COPR is unavailable for your AMI version, run Caddy as a container instead
(`caddy:2` image with `--network host`).

### 4.4 Reaching the database from your Windows machine

Install the [AWS CLI](https://aws.amazon.com/cli/) and the
[Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html),
then open a tunnel. It forwards `localhost:5433` on your PC to RDS, through the instance:

```powershell
aws ssm start-session `
  --target i-0123456789abcdef0 `
  --document-name AWS-StartPortForwardingSessionToRemoteHost `
  --parameters host=tactum-db.xxxxxxxx.mx-central-1.rds.amazonaws.com,portNumber=5432,localPortNumber=5433
```

While it's open, connect any client (psql, DBeaver, pgAdmin) to `localhost:5433`. Through the
tunnel, the hostname doesn't match the certificate. Use `DATABASE_SSL=require` (encrypted, but
without the hostname check) for commands you run from your PC.

---

## 5. Deploy the API

### 5.1 Code and CA certificate

On the instance (Session Manager):

```bash
sudo mkdir -p /opt/tactum && sudo chown ssm-user /opt/tactum && cd /opt/tactum
git clone <tactum-api repository url> api
sudo mkdir -p /etc/ssl/rds
sudo curl -fsSL -o /etc/ssl/rds/global-bundle.pem \
  https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem
```

### 5.2 Configuration

Create `/opt/tactum/api/.env` (readable only by you: `chmod 600 .env`):

```dotenv
ENVIRONMENT=production
DATABASE_URL=postgresql://tactum_app:<password 2>@tactum-db.xxxxxxxx.mx-central-1.rds.amazonaws.com:5432/tactum
DATABASE_SSL=verify-full
DATABASE_SSL_ROOT_CERT=/etc/ssl/rds/global-bundle.pem
CORS_ORIGINS=https://tactum.mx,https://www.tactum.mx
STRIPE_SECRET_KEY=sk_live_...
STRIPE_WEBHOOK_SECRET=whsec_...        # filled in at step 7
PRODUCT_TOTAL_PRICE_MXN=875
DEPOSIT_AMOUNT_MXN=300
```

URL-encode the password in `DATABASE_URL` if it contains characters such as `@`, `:` or `/`.

### 5.3 Run the migrations

Migrations run as `tactum_owner`, so the tables belong to it and `tactum_app` gets row access
through the default privileges from step 3.3:

```bash
cd /opt/tactum/api
docker build -t tactum-api .
docker run --rm --env-file .env \
  -e DATABASE_URL='postgresql://tactum_owner:<password 1>@tactum-db.xxxxxxxx.mx-central-1.rds.amazonaws.com:5432/tactum' \
  -v /etc/ssl/rds:/etc/ssl/rds:ro \
  tactum-api python -m scripts.migrate
```

### 5.4 Start the API

```bash
docker run -d --name tactum-api --restart unless-stopped \
  --env-file .env \
  -v /etc/ssl/rds:/etc/ssl/rds:ro \
  -p 127.0.0.1:8000:8000 \
  tactum-api
```

`-p 127.0.0.1:8000:8000` keeps the container reachable only from the instance itself. The
internet reaches it only through Caddy.

### 5.5 HTTPS with Caddy

`/etc/caddy/Caddyfile`:

```
api.tactum.mx {
    reverse_proxy 127.0.0.1:8000
}
```

```bash
sudo systemctl enable --now caddy
sudo systemctl reload caddy
```

Caddy gets and renews the Let's Encrypt certificate by itself, once the DNS record from step 4.2
points at the instance.

### 5.6 Smoke test

```powershell
curl https://api.tactum.mx/health            # {"status":"ok"}
curl https://api.tactum.mx/health/db         # {"status":"ok"} means the database is reachable
curl https://api.tactum.mx/reservations/count
curl -N https://api.tactum.mx/reservations/count/stream   # should print data: {"count": ...}
```

### Updating later

```bash
cd /opt/tactum/api && git pull
docker build -t tactum-api .
# run migrations (5.3) if the release has any, then:
docker rm -f tactum-api && <the docker run command from 5.4>
```

---

## 6. Copy the data from MongoDB (rehearsal)

`scripts/migrate_from_mongo.py` copies `users` and `reservations`. It is safe to run several
times: users are matched by email and reservations by MongoDB id or Stripe deposit id, so a
second run updates rows instead of duplicating them. Do a rehearsal now, and run it again at
cutover (step 9).

Run it from your PC through the tunnel (step 4.4), as the owner role:

```powershell
cd C:\Users\KARIN\Documents\programming\tactum-api
$env:DATABASE_URL = 'postgresql://tactum_owner:<password 1>@localhost:5433/tactum'
$env:DATABASE_SSL = 'require'
$env:MONGODB_URI  = '<value of MONGODB_EARLYADOPTERS_URI from the Next.js app>'

uv run --group migration python -m scripts.migrate_from_mongo --dry-run
uv run --group migration python -m scripts.migrate_from_mongo
```

The Mongo URI must include the database name (`...mongodb.net/<db>?...`). The script uses it to
find the collections.

Check the result: the counts should match MongoDB, minus any users the script reported as skipped
because they had no email:

```sql
SELECT user_type, count(*) FROM users GROUP BY user_type;
SELECT status, payment_status, count(*) FROM reservations GROUP BY 1, 2;
SELECT count(*) FROM reservations r LEFT JOIN users u ON u.id = r.user_id WHERE u.id IS NULL; -- 0
```

What the script converts:

- Emails are trimmed and lowercased.
- `phone` changes from a Number to text.
- MongoDB `_id`s are kept in `legacy_mongo_id`, for traceability.
- Values outside the allowed enums fall back to the defaults.
- `createdAt`/`updatedAt` are preserved.

---

## 7. Point Stripe at the new webhook

In the Stripe Dashboard → *Developers* → *Webhooks* → **Add endpoint**:

- URL: `https://api.tactum.mx/webhooks/stripe`
- Events: `payment_intent.succeeded`
- API version: the latest. The handler only reads fields that are stable across versions.

Copy its signing secret (`whsec_...`) into `STRIPE_WEBHOOK_SECRET` on the instance, and restart
the container (5.4).

**Keep the old Next.js endpoint enabled for now.** With both active, every deposit is written to
MongoDB *and* PostgreSQL. That keeps the old system current, so you can roll back (step 11). The
final data copy then reconciles both, because reservations are matched by deposit id.

Test it with the Stripe CLI (`stripe trigger payment_intent.succeeded`), or with a test-mode
payment against a test-mode endpoint.

---

## 8. Update the Next.js app

Do this on a branch in `tactum-website`.

### 8.1 Environment variables (Vercel and `.env.local`)

```dotenv
API_URL=https://api.tactum.mx              # used by server code (the server action)
NEXT_PUBLIC_API_URL=https://api.tactum.mx  # used in the browser (Stripe flow, live counter)
```

To test a Vercel preview deployment, temporarily add its URL to `CORS_ORIGINS` on the API.

### 8.2 `src/server/actions/users.ts`

Keep it as a server action, so the form keeps working without JavaScript and the browser never
calls the API for this. Replace the Mongoose code with a call to `POST /leads`:

```ts
"use server";

import { hasLocale } from "next-intl";
import { getTranslations } from "next-intl/server";
import { routing } from "@/i18n/routing";
import type { FormState } from "@/shared/types/forms";

export const createUser = async (
  initialState: FormState,
  formData: FormData,
): Promise<FormState> => {
  const rawLocale = formData.get("locale")?.toString();
  const locale = hasLocale(routing.locales, rawLocale)
    ? rawLocale
    : routing.defaultLocale;
  const t = await getTranslations({ locale, namespace: "Errors" });

  const email = formData.get("email")?.toString().trim();
  if (!email) {
    return { success: false, message: t("emailRequired") };
  }

  try {
    const response = await fetch(`${process.env.API_URL}/leads`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        firstName: formData.get("first_name")?.toString(),
        lastName: formData.get("last_name")?.toString(),
        email,
        phone: formData.get("phone")?.toString(),
        city: formData.get("city")?.toString(),
      }),
      cache: "no-store",
    });

    if (response.ok) {
      return { success: true, message: t("subscribed") };
    }
    if (response.status === 422) {
      // Optionally map body.fieldErrors to messages next to each field.
      return { success: false, message: t("invalidFields") };
    }
    console.error("Lead API error", response.status, await response.text());
    return { success: false, message: t("generic") };
  } catch (err) {
    console.error("Lead API unreachable", err);
    return { success: false, message: t("generic") };
  }
};
```

Add `"invalidFields"` to the `Errors` namespace in `messages/en.json` and `messages/es.json`.
`duplicateEmail` can be removed: re-subscribing with the same email updates the user instead of
failing, as it did before.

Check `FormState` against this return type. The current action returns `{ success, message }`.

### 8.3 Payment intent (`form.tsx` and `stripePaymentElement.tsx`)

Change the URL, and stop sending amounts. The API rejects unknown fields with `422`, and the
price is now decided only on the server:

```ts
const response = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/payment-intents`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ email, name, phone, city, paymentIntentId }),
});
if (!response.ok) {
  // show the payment error state and a retry action
}
const { clientSecret, paymentIntentId: id } = await response.json();
```

In `form.tsx`, the first call currently sends `totalPrice` and `depositAmount`. Remove them. The
first call can send only `{ email: "pending@example.com" }`, or nothing at all (`{}`).

### 8.4 Live counter (`src/components/layout/header.tsx`)

```ts
const eventSource = new EventSource(
  `${process.env.NEXT_PUBLIC_API_URL}/reservations/count/stream`,
);
```

The message format (`{"count": n}`) is unchanged. If the counter is unavailable, the API answers
`503`, which triggers the existing `onerror` handler that hides it.

### 8.5 Test the branch

Run the Next.js app against the deployed API (or a local one) and go through:

- [ ] The newsletter form, for a new email and the same email again.
- [ ] The reservation flow with a Stripe test card (`4242 4242 4242 4242`).
- [ ] The success page loads, and a reservation row appears in PostgreSQL.
- [ ] The header counter increases without a reload, in a second browser tab.
- [ ] Both languages show the right error messages (try an invalid email).

---

## 9. Cutover

Pick a quiet moment. Nothing needs to stop, because both webhooks are active.

1. Run the data copy again (step 6, without `--dry-run`) to bring over anything written since the
   rehearsal.
2. Merge the Next.js branch and deploy it to production on Vercel.
3. Run the data copy **once more**. This catches leads submitted in the minutes the old code was
   still live.
4. Repeat the checks from 8.5 against production (with a real small payment, or in test mode if
   you keep a test environment).
5. Watch the logs for a day: `docker logs -f tactum-api` on the instance, plus Stripe →
   *Webhooks* → the new endpoint's delivery history (all `200`).

---

## 10. Clean up

After a week or two without problems:

1. **Stripe:** disable, then delete, the old Next.js webhook endpoint.
2. **`tactum-website`:** delete these:
   - `src/app/api/payment-intent/`
   - `src/app/api/webhooks/`
   - `src/app/api/reservations/`
   - `src/server/models/`
   - `src/server/utils/mongoDBConnection.ts`

   Then `npm uninstall mongoose mongodb stripe`. Keep `@stripe/stripe-js` and
   `@stripe/react-stripe-js`, which the browser uses.
3. **Vercel:** remove `MONGODB_EARLYADOPTERS_URI`, `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET`
   from the project's environment variables.
4. **MongoDB Atlas:** take a final export (`mongodump`) and keep it somewhere safe. Then pause or
   delete the cluster.

---

## 11. Rollback

Until step 10, rolling back is just redeploying the previous frontend:

- **Vercel** → *Deployments* → the deployment from before the cutover → *Promote to Production*
  (or *Instant Rollback*).
- Because the old Stripe webhook stayed active, MongoDB already has every reservation made in the
  meantime.
- Leads submitted through the new form are only in PostgreSQL. Export them with
  `SELECT * FROM users WHERE created_at > '<cutover time>'` if you need them in MongoDB.

---

## 12. Behaviour changes to know about

These are deliberate differences from the current Next.js code:

- **The deposit charged is now `DEPOSIT_AMOUNT_MXN` (300 MXN).** The current route charges a
  hard-coded `amount: 1000`, which is **10 MXN**, while its metadata says the deposit is 300. If
  10 MXN was a test value, keep testing in Stripe test mode. If the real deposit should be
  different, change `DEPOSIT_AMOUNT_MXN`.
- **The recorded deposit is what Stripe actually collected** (`amount_received`), not the
  metadata value.
- **Prices can't come from the browser.** `/payment-intents` rejects `totalPrice`/`depositAmount`
  (`422`).
- **An intent can only be updated while it's still waiting for payment**, and only if it's a
  deposit intent. Otherwise the API returns `409`.
- **`userId` is no longer accepted** from the browser. It was always `"guest"` and could be
  forged.
- **Error responses never include internal details.** The Stripe routes used to return the raw
  `error.message`.
- **Re-subscribing an existing customer through the news form keeps them a customer**, and
  optional fields left empty don't erase what was saved before.
- **Phone numbers are stored as text**, so leading zeros and `+52` are kept.
- The live counter still counts **all** reservations, as before. To count only active ones,
  change `count_reservations` and the SQL in `ReservationCountBroadcaster._publish`.

---

## 13. Follow-ups

Not needed for the migration, but worth doing afterwards:

- **Rate limiting** on `POST /leads` and `POST /payment-intents`, e.g. with the `slowapi`
  package, or AWS WAF if you later put an Application Load Balancer or CloudFront in front.
- **Secrets from SSM Parameter Store** instead of the `.env` file on the instance.
- **CloudWatch:** ship container logs (`awslogs` Docker log driver), and add alarms on RDS CPU,
  free storage and connection count.
- **CI/CD:** a GitHub Action that runs `uv run pytest` and `ruff`, builds the image, and deploys
  through SSM `send-command`.
- **Collect the name and phone from the Stripe address element.** The webhook receives the email
  but not the name or phone, so customers created at checkout have no `first_name`. The
  frontend already sends both to `/payment-intents`. Adding them to the intent's metadata would
  let the webhook save them.
