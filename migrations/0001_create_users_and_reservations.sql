-- Users (leads and customers) and their glove reservations.
-- Constraint names are spelled out so they are the same in every environment.

CREATE TABLE users (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    -- Always stored trimmed and lowercased, so a plain unique constraint is enough.
    email varchar(254) NOT NULL,
    -- Customers created by the Stripe webhook may arrive with only an email.
    first_name varchar(100),
    last_name varchar(100),
    phone varchar(30),
    city varchar(100) NOT NULL DEFAULT 'Other',
    user_type varchar(20) NOT NULL DEFAULT 'lead',
    stripe_customer_id varchar(255),
    -- The MongoDB ObjectId this row was copied from; lets the data copy be re-run safely.
    legacy_mongo_id varchar(24),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pk_users PRIMARY KEY (id),
    CONSTRAINT uq_users_email UNIQUE (email),
    CONSTRAINT uq_users_stripe_customer_id UNIQUE (stripe_customer_id),
    CONSTRAINT uq_users_legacy_mongo_id UNIQUE (legacy_mongo_id),
    CONSTRAINT ck_users_user_type CHECK (user_type IN ('lead', 'customer'))
);

CREATE TABLE reservations (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL,
    status varchar(20) NOT NULL DEFAULT 'pending',
    -- Amounts are in MXN (not cents), with two decimals.
    total_price numeric(10, 2) NOT NULL,
    deposit_amount numeric(10, 2) NOT NULL,
    balance_amount numeric(10, 2) NOT NULL,
    currency varchar(3) NOT NULL DEFAULT 'MXN',
    stripe_customer_id varchar(255),
    -- Unique so a webhook delivered twice updates the same reservation instead of duplicating it.
    deposit_payment_intent_id varchar(255),
    balance_payment_intent_id varchar(255),
    payment_status varchar(20) NOT NULL DEFAULT 'unpaid',
    reservation_date timestamptz NOT NULL,
    city varchar(100),
    metadata jsonb,
    legacy_mongo_id varchar(24),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pk_reservations PRIMARY KEY (id),
    CONSTRAINT uq_reservations_deposit_payment_intent_id UNIQUE (deposit_payment_intent_id),
    CONSTRAINT uq_reservations_legacy_mongo_id UNIQUE (legacy_mongo_id),
    CONSTRAINT fk_reservations_user_id_users
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT ck_reservations_status
        CHECK (status IN ('pending', 'confirmed', 'completed', 'cancelled')),
    CONSTRAINT ck_reservations_payment_status
        CHECK (payment_status IN ('unpaid', 'deposit_paid', 'fully_paid', 'refunded'))
);

CREATE INDEX ix_reservations_user_id ON reservations (user_id);

-- Replaces the MongoDB change stream: tells the API (LISTEN reservations_changed)
-- to refresh the live counter after any write to the table.
CREATE FUNCTION notify_reservations_changed() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_notify('reservations_changed', '');
    RETURN NULL;
END;
$$;

CREATE TRIGGER reservations_changed
AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON reservations
FOR EACH STATEMENT EXECUTE FUNCTION notify_reservations_changed();
