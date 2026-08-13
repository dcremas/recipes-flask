# Recipes

The family recipe site, rebuilt from the Heroku app (`recipes-heroku`) for
self-hosting on the EC2 box behind nginx.

- **Live (pending DNS):** https://recipes.dustincremascoli.com
- **Replaces:** https://recipes-heroku-f8e401f0d73b.herokuapp.com/
- **Database:** the existing `recipes` database on the EC2 Postgres instance —
  the same rows the Heroku app was already using. No data was migrated or
  copied; only the schema corrections in `deploy/migrate.sql` were applied.

## Layout

```
app.py            application factory, config, error handlers
content.py        site copy (hero, feature blurbs, social links)
models.py         Authors, Recipes + display helpers
forms.py          WTForms definitions and validation
routes.py         all routes, including the legacy-URL redirects
templates/        Jinja templates; partials/ holds nav, footer, icons, field macro
static/           css, js, and recipe photos under img/recipes/
tests/            pytest suite (36 tests) — run against a clone, never production
deploy/           systemd unit, nginx vhost, provision/deploy scripts, SQL
```

## What changed from the Heroku version

Same feature set — home, recipe list, table view, recipe detail, login/logout,
account creation, recipe creation — plus author-scoped **edit and delete**,
which the original lacked entirely.

Fixes, each of which was a real defect:

| Problem | Behavior before | Now |
|---|---|---|
| Missing photo | `Onion Dip` rendered `<img src="/static/oniondip.jpg">` → 404, broken image in production | `Recipes.image_file` checks the file exists; templates fall back to a styled placeholder |
| `tips` NULL | `recipe.tips.split("\r\n")` → `AttributeError` → 500 | `_lines()` tolerates NULL and returns `[]` |
| Line endings | split on `\r\n` only, so `\n`-seeded rows rendered as one blob | normalizes `\r\n`, `\r`, `\n`, drops blank lines |
| Duplicate signup | unhandled `IntegrityError` → 500 | field-level form error, with an `IntegrityError` catch as backstop |
| Password rules | a 1-character password was accepted | minimum 10 characters |
| Email | never validated | `Email()` validator |
| `password_hashed` | `varchar(162)` — exactly the length of Werkzeug's scrypt output, zero headroom | `varchar(255)` |
| `joined_at` | `onupdate=utcnow`, so "joined" moved every write | set once, on insert |
| `Recipes(UserMixin)` | recipes had `is_authenticated`, `get_id()` | removed |
| Recipe list order | unordered | ordered by title / category |
| Errors | no handlers; tracebacks on 404 and 500 | 403/404/413/500 pages, session rolled back on 500 |
| Open redirect | n/a (no `next` support) | `next` is restricted to same-site relative paths |
| Print | home page promised print-friendly; no print CSS existed | real `@media print` rules on the recipe page |

## Local development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# Work against a throwaway clone, never the live database:
ssh ec2 'sudo -u postgres pg_dump --no-owner --no-acl -t authors -t recipes recipes' > /tmp/r.sql
createdb recipes_test && psql -q -d recipes_test -f /tmp/r.sql
psql -d recipes_test -v ON_ERROR_STOP=1 -f deploy/migrate.sql

DATABASE_URL=postgresql+psycopg:///recipes_test SECRET_KEY=dev SESSION_COOKIE_SECURE=0 \
  .venv/bin/python -c "from app import app; app.run(port=5090)"
```

Tests:

```bash
DATABASE_URL=postgresql+psycopg:///recipes_test SECRET_KEY=test .venv/bin/python -m pytest -q
```

The suite creates and deletes its own author and recipes. It expects a clone
containing at least one recipe by another author (for the ownership tests) and
one without a photo (for the placeholder test).

## Deploying

The target host and SSH key are not committed. Set them once:

```bash
cp deploy/deploy.env.example deploy/deploy.env   # gitignored
$EDITOR deploy/deploy.env
```

Then:

```bash
deploy/deploy.sh              # sync + restart
deploy/deploy.sh --migrate    # sync, then apply deploy/migrate.sql
deploy/deploy.sh --provision  # sync, then run provision.sh (first-time setup)
```

Application secrets live in `/etc/recipes/recipes.env` **on the server**,
root-owned and group-readable by the service account. They are never rsynced —
`deploy.sh` excludes `.env` explicitly, and `provision.sh` generates the session
key on the server rather than accepting one from a laptop.

See `deploy/README-deploy.md` for the server-side details.
