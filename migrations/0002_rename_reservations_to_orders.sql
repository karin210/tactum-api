-- Renames reservations to orders, along with every object named after the old table.
-- Renames keep the existing rows and indexes; the trigger and its channel are recreated.

ALTER TABLE reservations RENAME TO orders;
ALTER TABLE orders RENAME COLUMN reservation_date TO order_date;

ALTER TABLE orders RENAME CONSTRAINT pk_reservations TO pk_orders;
ALTER TABLE orders
    RENAME CONSTRAINT uq_reservations_deposit_payment_intent_id TO uq_orders_deposit_payment_intent_id;
ALTER TABLE orders RENAME CONSTRAINT uq_reservations_legacy_mongo_id TO uq_orders_legacy_mongo_id;
ALTER TABLE orders RENAME CONSTRAINT fk_reservations_user_id_users TO fk_orders_user_id_users;
ALTER TABLE orders RENAME CONSTRAINT ck_reservations_status TO ck_orders_status;
ALTER TABLE orders RENAME CONSTRAINT ck_reservations_payment_status TO ck_orders_payment_status;
ALTER INDEX ix_reservations_user_id RENAME TO ix_orders_user_id;

-- The API listens on the channel name, so it changes together with the trigger.
DROP TRIGGER reservations_changed ON orders;
DROP FUNCTION notify_reservations_changed();

CREATE FUNCTION notify_orders_changed() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_notify('orders_changed', '');
    RETURN NULL;
END;
$$;

CREATE TRIGGER orders_changed
AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON orders
FOR EACH STATEMENT EXECUTE FUNCTION notify_orders_changed();
