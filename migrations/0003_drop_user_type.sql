-- Leads are no longer handled, so every user is a customer and the type carries no information.
-- Rows that were leads are kept as plain users.

ALTER TABLE users DROP CONSTRAINT ck_users_user_type;
ALTER TABLE users DROP COLUMN user_type;
