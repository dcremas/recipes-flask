# Recipes

The family recipe collection — **https://recipes.dustincremascoli.com**

A Flask app self-hosted on EC2 behind nginx. Public to read and print, authored
only by the admin, with recipes that can be typed in or transcribed from a
photograph of a recipe card.

```
app.py            application factory, config, error handlers
content.py        site copy (hero, feature blurbs, social links)
models.py         Authors, Recipes + display helpers
photos.py         upload validation, resizing and storage
importer.py       recipe-from-photo transcription (Claude or Gemini vision)
forms.py          WTForms definitions and validation
routes.py         every route
templates/        Jinja templates; partials/ holds nav, footer, icons, field macro
static/           css, js, fonts, and the bundled recipe photos under img/recipes/
tests/            pytest suite (106 tests) — run against a clone, never production
deploy/           systemd unit, nginx vhost, provision/deploy scripts, SQL
```

Stack: Flask 3 · SQLAlchemy 2 · Flask-Login · WTForms · Postgres 16 (same box) ·
gunicorn (2 workers, unix socket) · WeasyPrint for PDFs · Pillow for photos ·
Anthropic or Gemini SDK for transcription, whichever is configured. No frontend
framework, no webfonts, no CDN — the CSS and JS are hand-written and served from
`static/`.

## Who can do what

**Anyone** can browse, filter, search, view and print every recipe. No account,
no sign-in prompt, nothing gated.

**One account** — whoever's email matches `ADMIN_EMAIL` — can add, edit and
delete. That's the whole permission model; there are no roles, no groups, and no
way to register.

- The admin console is **`/manage`**: every recipe in one table with a thumbnail,
  category and updated date, Edit and Delete on each row, a title/category
  search, and a count of recipes still missing a photo.
- **There is no signup.** No `SignupForm`, no `/register` route, no template for
  one. `/register` is a 404.
- **`/login` works but is unlinked** — deliberately unlisted rather than removed,
  so the admin can reach it directly and visitors are never shown a door they
  can't open.
- **Admin is decided by config, not a column.** The app connects as
  `recipes_app`, a DML-only Postgres role that cannot `ALTER TABLE`, so an
  `is_admin` column would need a migration run as the database owner. See
  `Authors.is_admin`.
- **With `ADMIN_EMAIL` unset, nobody can author.** Deliberate: the failure mode
  of a missing setting must be "no one" rather than "everyone", and the public
  site keeps serving either way. A mismatched value fails the same way and is
  silent — login succeeds, then every authoring route 403s and no Add-recipe
  link renders. If authoring has mysteriously stopped, check this first.
- **Enforcement is `@admin_required` on the route.** Templates hide the buttons
  too, but that's cosmetic; the server is the authority.
- Other rows may exist in `authors` and can still sign in. They can do nothing.

## Routes

| Route | Methods | Who | Notes |
|---|---|---|---|
| `/`, `/home` | GET | public | hero, stats, six newest recipes |
| `/recipes` | GET | public | card grid; `?category=` filters |
| `/recipes/table` | GET | public | dense table view |
| `/recipes/<id>` | GET | public | recipe detail |
| `/recipes/<id>.pdf` | GET | public | one-page PDF, generated on demand |
| `/media/<file>` | GET | public | uploaded photos (nginx serves these directly) |
| `/login`, `/logout` | GET, POST | — | unlinked; rate limited |
| `/manage` | GET | admin | the console |
| `/recipes/new` | GET, POST | admin | create |
| `/recipes/<id>/edit` | GET, POST | admin | edit |
| `/recipes/<id>/delete` | POST | admin | POST + CSRF only — never a link |
| `/recipes/import` | GET, POST | admin | transcribe a photo |
| `/health` | GET | ops | liveness only; does not touch Postgres, so nginx and systemd can tell "app down" from "database down" |

Four addresses that predate the current URL scheme still resolve, so existing
links and bookmarks keep working: `/create_recipe` → `/recipes/new`,
`/recipes_table` → `/recipes/table`, and `/<id>/` → `/recipes/<id>` (301);
`/create_account` → the home page (302, not 301, so a cached permanent redirect
doesn't outlive the decision).

## Configuration

Read from the environment; in production from `/etc/recipes/recipes.env`, loaded
once by systemd's `EnvironmentFile` — **editing it needs
`systemctl restart recipes-web`**.

| Variable | Required | Purpose |
|---|---|---|
| `DATABASE_URL` | yes | `postgresql+psycopg://recipes_app:…@127.0.0.1:5432/recipes`. `EXTERNAL_URL` is honored as a fallback, and a `postgres://` scheme is rewritten for SQLAlchemy 2. |
| `SECRET_KEY` | yes in prod | Session signing. Missing in production raises at boot; elsewhere a random key is generated with a warning (sessions then reset on restart). |
| `ADMIN_EMAIL` | yes to author | The one account allowed to add/edit/delete. Compared lowercase against `authors.email`. |
| `UPLOAD_DIR` | yes for photos | Where uploads are written. **Must be outside the app tree** — see Photos. Defaults to the instance folder for local work. |
| `IMPORT_PROVIDER` | no | Who transcribes: `gemini`, `anthropic`, or `auto` (default). `auto` uses whichever key is set, preferring Gemini. A named provider without its key leaves import **off** rather than falling back to the other account. |
| `GEMINI_API_KEY` | no | Enables import-from-photo on Google. `GOOGLE_API_KEY` is accepted as an alias. |
| `ANTHROPIC_API_KEY` | no | Enables import-from-photo on Claude. Both keys blank hides the feature entirely. |
| `IMPORT_MODEL_GEMINI` | no | Defaults to `gemini-3.7-flash`. Pin a version, not the `gemini-flash-latest` alias — an alias that moves changes behavior and price with no deploy and no log line. |
| `IMPORT_MODEL_ANTHROPIC` | no | Defaults to `claude-opus-5`. `IMPORT_MODEL` is the old name and is still honored. |
| `GEMINI_USE_VERTEX` | no | `1` bills Gemini through Vertex AI on Application Default Credentials instead of an API key — the path GCP credits apply to. Needs `GOOGLE_CLOUD_PROJECT` (and usually `GOOGLE_APPLICATION_CREDENTIALS`); `GOOGLE_CLOUD_LOCATION` defaults to `us-central1`. |
| `SESSION_COOKIE_SECURE` | no | Set `0` only when serving plain HTTP, or cookies won't work. |

`MAX_CONTENT_LENGTH` is 12 MB in code; nginx's `client_max_body_size` is 14 MB.
nginx must stay the larger of the two, or it rejects the request itself and the
app's 413 page never renders.

## Data model

Two tables, `authors` and `recipes`, in the `recipes` database on the same host.

`Recipes` holds `title`, `category`, `prep_time`, `cooking_time`,
`yield_amount`, `ingredients`, `instructions`, `tips`, `image_filename`,
`author_id` and `timestamp`. Ingredients, instructions and tips are plain text,
one item per line — `models._lines()` splits them for display, normalizing
`\r\n`, `\r` and `\n` and dropping blank lines, and tolerating NULL (`tips` is
genuinely optional).

`deploy/migrate.sql` is additive, widening-only and idempotent — safe to re-run
after any pull. Apply it with `deploy/deploy.sh --migrate`.

## Photos

Each recipe can carry a photo of the finished dish, shown on the recipe page,
the cards, the console thumbnails and the PDF.

Two things about where files live are load-bearing and easy to get wrong:

- **Uploads go to `UPLOAD_DIR` (`/var/lib/recipes/uploads`), outside the
  application tree.** Not a matter of taste: the systemd unit sets
  `ProtectHome=read-only`, so the app *cannot* write anywhere under
  `~/recipes_flask`, and `deploy.sh` rsyncs with `--delete`, so anything written
  under `static/` would be erased by the next deploy.
- **The unit therefore needs `ReadWritePaths=/var/lib/recipes`**, and nginx needs
  the `location /media/` alias. Without the first, uploads fail with `EROFS`
  after the form has already validated; without the second, photos 404 in
  production while working fine under `flask run`. Both failures are silent, so
  if you rebuild the host, verify a real upload rather than assuming.

Every upload is decoded by Pillow, rotated per its EXIF orientation tag, stripped
of metadata (including GPS), capped at 1600px on the long edge, and re-encoded as
JPEG. The extension is never trusted — the decoder decides what a file is, so a
script renamed `.jpg` cannot survive the round trip. Filenames are
content-addressed (`<slug>-<sha256[:12]>.jpg`): replacement yields a new URL that
is safe to cache immutably, re-saving identical bytes is idempotent, and two
recipes whose titles slugify alike cannot collide on one file.

`recipes.image_filename` stores the bare filename, never a path. Seven photos
predating uploads are still resolved from `static/img/recipes/<slug>.jpg` as a
fallback; an upload always wins, and a row pointing at a missing file falls back
to the styled placeholder rather than shipping a broken `<img>`.

## Import a recipe from a photo

`/recipes/import` reads a photograph of a recipe card, cookbook page or printout
into the recipe form — Claude vision with a JSON schema, in `importer.py`.

**The extraction is a draft, never a write.** It goes into the session, seeds
`/recipes/new`, and appears behind a "check it before saving" banner along with
anything the model flagged as uncertain. Nothing reaches the database until the
normal form is submitted and validated, because OCR of handwriting gets
quantities wrong in ways you only discover in the kitchen. The prompt tells the
model to transcribe rather than improve, to leave a field blank instead of
inferring it, and to set `unreadable` rather than guess at a bad photo.

The image is normalized by `photos.normalize()` first, which caps resolution (so
cost is predictable), strips EXIF, and rejects non-images before anything is sent
anywhere. The photo is sent to whichever API is configured and is not stored on
the server. Cost per import depends on the provider and model in use — check
Billing → Reports against the actual SKU rather than trusting a number written
here, since the model can be repinned without this file changing.

Two providers can do the transcription — Claude or Gemini — because for this job
they are interchangeable: one image in, one JSON object out, validated against
the same Pydantic schema either way. `IMPORT_PROVIDER` chooses; the SDKs are
imported inside their own branch, so only the one in use has to be installed and
neither can stop the site booting. Switching providers is an env edit and a
`systemctl restart recipes-web`, not a deploy.

Two rules the code enforces because both failure modes are silent and cost money:
a provider named explicitly is **never** replaced by a fallback if its key is
missing (import just goes off, naming the variable to set), and model pins are
per-provider so a leftover `IMPORT_MODEL_GEMINI` cannot be sent to Anthropic. The
page's privacy line names whichever service is actually receiving the photo.

Enabled by either key. With neither, the feature is hidden from the console and
`/recipes/import` returns an explained 503 rather than a traceback.

## Behavior worth knowing

- **PDFs are real files, not `window.print()`.** `/recipes/<id>.pdf` renders
  through WeasyPrint and always produces exactly one page: `_FIT_STEPS` walks a
  ladder of font sizes and margins, re-laying out until the document reports a
  single page. Identical output for every visitor regardless of browser or print
  settings. WeasyPrint is imported lazily — it pulls pango/cairo through cffi, so
  only the requests that want a PDF pay for it. The stylesheet is inlined and the
  photo passed as a `file://` URI, so rendering never depends on the site being
  reachable from itself.
- **Deletion is POST-only with a CSRF token, everywhere.** A GET delete can be
  fired by a crawler or a prefetching browser.
- **`?next=` is restricted to same-site relative paths** (`_safe_next`), so the
  login form can't be turned into an open redirect for phishing.
- **Login is rate limited twice**: nginx in front (`limit_req`, the authoritative
  one) and an in-process backstop in `routes._rate_limited`, which is per-worker
  and deliberately dependency-free. Import has its own bucket.
- **Error handlers exist for 403/404/413/500**, and the 500 handler rolls back
  the session — otherwise one failed transaction makes every later request on
  that worker fail too.
- **The service runs `User=ec2-user` but `Group=nginx` on purpose.** With
  `Group=ec2-user` the runtime directory is `0750 ec2-user:ec2-user` and every
  proxied request fails `13: Permission denied` on the socket while the app
  itself still reports healthy.
- **Theming**: dark by default, a saved preference wins, applied before first
  paint so there's no flash. All JS is progressive enhancement — the site is
  fully usable with it off.
- **Print CSS** exists as well as the PDF, for anyone who prints the page itself.

## Local development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# Work against a throwaway clone, never the live database:
ssh ec2 'sudo -u postgres pg_dump --no-owner --no-acl -t authors -t recipes recipes' > /tmp/r.sql
createdb recipes_test && psql -q -d recipes_test -f /tmp/r.sql
psql -d recipes_test -v ON_ERROR_STOP=1 -f deploy/migrate.sql

DATABASE_URL=postgresql+psycopg:///recipes_test SECRET_KEY=dev SESSION_COOKIE_SECURE=0 \
  ADMIN_EMAIL=you@example.com UPLOAD_DIR=/tmp/recipes-uploads \
  .venv/bin/python -c "from app import app; app.run(port=5090)"
```

To exercise the admin UI locally you need a matching row — set a password on the
`ADMIN_EMAIL` account in the clone with `Authors.set_password()`. Don't leave a
test account in the clone using an address the test suite also uses; the unique
index on `lower(email)` will then break every fixture.

## Tests

```bash
DATABASE_URL=postgresql+psycopg:///recipes_test SECRET_KEY=test .venv/bin/python -m pytest -q
```

106 tests against a real Postgres clone — no mocked database. The suite creates
and deletes its own authors, recipes and photo files, pins `ADMIN_EMAIL` to its
own fixture address so the admin gate is tested in both directions, points
`UPLOAD_DIR` at a per-test `tmp_path`, and leaves both transcription keys empty so no
test can reach a real API (the import tests monkeypatch `importer.extract`).

It expects a clone containing at least one recipe by another author and one
without a photo. Image fixtures are genuinely encoded via Pillow, since
`photos.normalize()` decodes what it's given and fake bytes would only ever
exercise the rejection path.

## Deploying

Host and SSH key are not committed:

```bash
cp deploy/deploy.env.example deploy/deploy.env   # gitignored
$EDITOR deploy/deploy.env
```

```bash
deploy/deploy.sh              # sync + pip install + restart
deploy/deploy.sh --migrate    # sync, then apply deploy/migrate.sql, then restart
deploy/deploy.sh --provision  # sync, then run provision.sh on the server
```

**`deploy.sh` alone does not install the systemd unit or the nginx vhost** — only
`--provision` does. If you change `deploy/recipes-web.service` or
`deploy/nginx-recipes.conf`, a plain deploy will rsync the file and change
nothing. `provision.sh` is idempotent and safe to re-run; it validates with
`nginx -t` before reloading and fails loudly if the service doesn't come back.

When a change adds a column, run `--migrate` **before** the new code serves
traffic. `deploy.sh` orders this correctly (rsync → migrate → restart): the old
process keeps serving old code until the restart, so there's no window where new
code queries a column that doesn't exist yet.

Secrets live in `/etc/recipes/recipes.env` **on the server**, root-owned and
group-readable by the service account. They are never rsynced — `deploy.sh`
excludes `.env`, and `provision.sh` generates the session key on the server
rather than accepting one from a laptop. `provision.sh` leaves an existing env
file alone, so new variables have to be added by hand.

See `deploy/README-deploy.md` for server-side details.
