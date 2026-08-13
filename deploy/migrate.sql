-- Schema corrections for the existing `recipes` database.
--
-- Additive and widening only: no column is dropped, no data is rewritten, and
-- every statement is safe to run twice. The file wraps itself in a transaction,
-- so do NOT add psql's -1 flag (that would nest and emit warnings):
--
--     sudo -u postgres psql -d recipes -v ON_ERROR_STOP=1 -f migrate.sql
--
-- Widening a varchar's length limit is a catalog-only change in PostgreSQL —
-- it does not rewrite the table and does not take a long lock.

BEGIN;

-- 1. password_hashed: varchar(162) is exactly the length of Werkzeug's current
--    scrypt output ("scrypt:32768:8:1$" + 16-char salt + 128 hex chars). Zero
--    headroom: any upstream parameter change starts raising DataError on
--    signup and password change.
ALTER TABLE authors ALTER COLUMN password_hashed TYPE varchar(255);

-- 2. Recipe titles were capped at 25 characters, which "Slow Cooker Pulled
--    Pork Sandwiches" already exceeds. Category was equally tight.
ALTER TABLE recipes ALTER COLUMN title    TYPE varchar(120);
ALTER TABLE recipes ALTER COLUMN category TYPE varchar(60);

-- 3. Identity columns were nullable, so a NULL username or email could be
--    inserted and would then break login and display. Backfill defensively
--    before enforcing, so this cannot fail on unexpected data.
UPDATE authors SET username = 'author_' || id WHERE username IS NULL;
UPDATE authors SET email    = 'author_' || id || '@invalid.local' WHERE email IS NULL;
UPDATE authors SET password_hashed = '' WHERE password_hashed IS NULL;

ALTER TABLE authors ALTER COLUMN username        SET NOT NULL;
ALTER TABLE authors ALTER COLUMN email           SET NOT NULL;
ALTER TABLE authors ALTER COLUMN password_hashed SET NOT NULL;

-- 4. The foreign key had no supporting index, so "recipes by this author" was
--    a sequential scan and ON DELETE checks scanned the child table.
CREATE INDEX IF NOT EXISTS ix_recipes_author_id ON recipes (author_id);

-- 5. Citext would be the tidier fix for case-insensitive identity, but it needs
--    an extension. A functional unique index achieves the same guarantee and
--    stops 'Dustin' and 'dustin' both existing.
CREATE UNIQUE INDEX IF NOT EXISTS ux_authors_email_lower    ON authors (lower(email));
CREATE UNIQUE INDEX IF NOT EXISTS ux_authors_username_lower ON authors (lower(username));

-- 6. Uploaded photo, stored as a bare filename inside UPLOAD_DIR — never a path,
--    so a stored value can never escape that directory.
--
--    A column rather than the old slug-derived lookup: deriving the filename
--    from the title meant renaming a recipe silently orphaned its photo, and two
--    recipes whose titles slugify alike ("Mac & Cheese" / "Mac Cheese") collided
--    on one file. NULL means "no upload" — the bundled static/img/recipes photo
--    is then used if one exists, so nothing that renders today stops rendering.
ALTER TABLE recipes ADD COLUMN IF NOT EXISTS image_filename varchar(255);

COMMIT;
