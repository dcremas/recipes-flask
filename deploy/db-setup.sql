-- Least-privilege database role for the app.
--
-- The Heroku app connected as a superuser-ish owner. This role can read and
-- write the two application tables and nothing else: it cannot create, drop or
-- alter anything, so a SQL-injection bug cannot reshape the schema.
--
--   sudo -u postgres psql -d recipes -v ON_ERROR_STOP=1 -f db-setup.sql
--
-- Set the password afterwards, out of shell history:
--   sudo -u postgres psql -d recipes -c "\password recipes_app"

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'recipes_app') THEN
    CREATE ROLE recipes_app LOGIN;
  END IF;
END
$$;

GRANT CONNECT ON DATABASE recipes TO recipes_app;
GRANT USAGE  ON SCHEMA public     TO recipes_app;

GRANT SELECT, INSERT, UPDATE, DELETE ON authors, recipes TO recipes_app;
GRANT USAGE, SELECT ON SEQUENCE authors_id_seq, recipes_id_seq TO recipes_app;

-- Explicitly not granted: CREATE on the schema, and any rights on
-- recipes_backup. The app has no business touching either.
REVOKE CREATE ON SCHEMA public FROM recipes_app;
