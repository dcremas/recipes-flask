"""Routes.

Every URL the Heroku app served still resolves — the renamed ones via 301, so
existing links and bookmarks keep working:

    /create_recipe   -> /recipes/new
    /recipes_table   -> /recipes/table
    /<id>/           -> /recipes/<id>

The one exception is /create_account, which used to open a signup form. Public
registration is gone, so it now redirects to the home page instead of 404ing an
address that is still linked from elsewhere on the internet.

Authoring is admin-only, enforced by @admin_required on the server rather than
by hiding buttons in the template.
"""

from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask import send_from_directory
from flask_login import current_user, login_required, login_user, logout_user

import photos
from app import db, login_manager
from content import FEATURES, HERO
from forms import DeleteForm, LoginForm, RecipeForm, ScanForm
from models import Authors, Recipes

bp = Blueprint("main", __name__)


def admin_required(view):
    """Login, then admin. Anything else is a 403.

    Wrapping login_required rather than replacing it keeps the useful
    distinction between the two failures: a signed-out visitor is sent to the
    login form, a signed-in non-admin is told no.
    """

    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not getattr(current_user, "is_admin", False):
            abort(403)
        return view(*args, **kwargs)

    return wrapped


@login_manager.user_loader
def load_user(user_id: str):
    # db.session.get is the SQLAlchemy 2.x spelling; Query.get is deprecated.
    try:
        return db.session.get(Authors, int(user_id))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
# Deliberately in-process and dependency-free. One gunicorn worker per box makes
# this accurate; with several workers it becomes per-worker, which is still a
# meaningful brake. nginx does the heavier limiting in front (see
# deploy/nginx-recipes.conf) — this is the backstop, not the front line.
_ATTEMPTS: dict[str, deque] = defaultdict(deque)


def _rate_limited(bucket: str, limit: int, window: int) -> bool:
    key = f"{bucket}:{request.remote_addr or 'unknown'}"
    now = time.monotonic()
    hits = _ATTEMPTS[key]
    while hits and now - hits[0] > window:
        hits.popleft()
    if len(hits) >= limit:
        return True
    hits.append(now)
    return False


def _safe_next(target: str | None) -> str | None:
    """Only allow same-site relative redirects.

    Without this, /login?next=https://evil.example is an open redirect that
    lends the site's name to a phishing page.
    """
    if not target:
        return None
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return None
    if not target.startswith("/") or target.startswith("//"):
        return None
    return target


# ---------------------------------------------------------------------------
# Public pages
# ---------------------------------------------------------------------------
@bp.route("/")
@bp.route("/home")
def home():
    # The original hardcoded six placeholder cards pointing at "#". These are
    # the real newest recipes instead.
    featured = (
        db.session.execute(
            db.select(Recipes).order_by(Recipes.timestamp.desc().nullslast()).limit(6)
        )
        .scalars()
        .all()
    )
    total = db.session.scalar(db.select(db.func.count()).select_from(Recipes)) or 0
    category_count = (
        db.session.scalar(
            db.select(db.func.count(db.distinct(db.func.lower(Recipes.category))))
        )
        or 0
    )
    return render_template(
        "home.html",
        hero=HERO,
        features=FEATURES,
        featured=featured,
        total=total,
        category_count=category_count,
    )


@bp.route("/recipes")
def recipes():
    category = (request.args.get("category") or "").strip()
    query = db.select(Recipes).order_by(Recipes.title.asc())
    if category:
        query = query.where(Recipes.category.ilike(category))
    items = db.session.execute(query).scalars().all()

    categories = (
        db.session.execute(
            db.select(Recipes.category).distinct().order_by(Recipes.category.asc())
        )
        .scalars()
        .all()
    )
    return render_template(
        "recipes.html", recipes=items, categories=categories, active_category=category
    )


@bp.route("/recipes/table")
def recipes_table():
    items = (
        db.session.execute(db.select(Recipes).order_by(Recipes.category, Recipes.title))
        .scalars()
        .all()
    )
    return render_template("recipes_table.html", recipes=items)


@bp.route("/recipes/<int:recipe_id>")
def recipe(recipe_id: int):
    item = db.session.get(Recipes, recipe_id)
    if item is None:
        abort(404)
    return render_template("recipe.html", recipe=item, delete_form=DeleteForm())


@bp.route("/media/<path:filename>")
def media(filename: str):
    """Serve an uploaded photo.

    In production nginx aliases /media/ straight to UPLOAD_DIR and this never
    runs; it exists so `flask run` works with no web server in front, and as a
    fallback if the nginx alias is ever missing. send_from_directory rejects any
    filename that escapes the directory, so traversal is handled for us.
    """
    return send_from_directory(
        current_app.config["UPLOAD_DIR"],
        filename,
        max_age=31536000,  # content-addressed names: safe to cache forever
    )


@bp.route("/recipes/<int:recipe_id>.pdf")
def recipe_pdf(recipe_id: int):
    """Render the recipe as a real PDF file.

    WeasyPrint is imported lazily: it pulls in pango/cairo through cffi, which
    costs both import time and resident memory in every worker. Only the
    handful of requests that actually want a PDF should pay for it.
    """
    item = db.session.get(Recipes, recipe_id)
    if item is None:
        abort(404)

    pdf = _render_pdf(item)
    filename = f"{item.slug or 'recipe'}.pdf"
    return Response(
        pdf,
        mimetype="application/pdf",
        headers={
            # inline, not attachment: the browser's PDF viewer opens it, from
            # which the reader can print or save. An attachment forces a
            # download and hides the result, which is the opposite of what a
            # "print this recipe" button should do.
            "Content-Disposition": f'inline; filename="{filename}"',
            "Cache-Control": "public, max-age=300",
        },
    )


# Auto-fit ladder. Each step is (root font size in pt, page margin in mm).
# 10.5pt is the comfortable default and fits every recipe currently in the
# collection; the smaller steps exist for unusually long ones. Margins shrink
# alongside the type so a dense page still looks deliberate rather than crammed.
_FIT_STEPS = [
    (10.5, 17),
    (10.0, 16),
    (9.5, 15),
    (9.0, 14),
    (8.5, 13),
    (8.0, 12),
    (7.5, 11),
    (7.0, 10),
    (6.5, 10),
    (6.0, 10),
]


def _render_pdf(item: Recipes) -> bytes:
    """Render the recipe to a single-page PDF.

    WeasyPrint lays out the whole document before it can report a page count,
    so fitting to one page means rendering and measuring. The ladder above is
    walked until the document reports a single page; each step is one full
    layout pass, which is why the default sits at the top and virtually every
    recipe stops there on the first try.
    """
    from weasyprint import HTML  # noqa: PLC0415 — see recipe_pdf() docstring

    static_dir = os.path.join(current_app.root_path, "static")

    # The stylesheet is inlined and the photo passed as a file:// URI so the
    # renderer never makes a network request — it must not depend on the site
    # being reachable from itself.
    with open(os.path.join(static_dir, "css", "pdf.css"), encoding="utf-8") as fh:
        pdf_css = fh.read()

    photo_uri = None
    photo_path = item.photo_path
    if photo_path:
        photo_uri = Path(photo_path).as_uri()

    document = None
    for base_pt, margin_mm in _FIT_STEPS:
        html = render_template(
            "recipe_pdf.html",
            recipe=item,
            pdf_css=pdf_css,
            photo_uri=photo_uri,
            base_pt=base_pt,
            margin_mm=margin_mm,
        )
        document = HTML(string=html, base_url=static_dir).render()
        if len(document.pages) == 1:
            break
    else:
        # Fell off the end of the ladder. One page is a hard requirement, so the
        # first page is what ships — which means content past it is dropped.
        # That is a real trade-off, logged loudly rather than hidden: at 6pt a
        # recipe would need roughly 60 ingredients and 40 steps to get here, so
        # in practice this is a tripwire, not a code path.
        current_app.logger.warning(
            "recipe %s (%r) overflows one page even at the smallest size "
            "(%d pages rendered); content beyond page 1 is not included",
            item.id,
            item.title,
            len(document.pages),
        )

    # Single page, always — the guarantee holds regardless of which branch ran.
    return document.copy(document.pages[:1]).write_pdf()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.home"))

    form = LoginForm()
    if form.validate_on_submit():
        if _rate_limited("login", limit=8, window=300):
            flash("Too many attempts. Please wait a few minutes.", "error")
            return render_template("login.html", form=form), 429

        author = db.session.execute(
            db.select(Authors).where(
                db.func.lower(Authors.email) == form.email.data.strip().lower()
            )
        ).scalar_one_or_none()

        # One message for both "no such account" and "wrong password", so the
        # form cannot be used to enumerate which emails are registered.
        if author is None or not author.check_password(form.password.data):
            flash("Incorrect email or password.", "error")
            return render_template("login.html", form=form), 401

        login_user(author, remember=form.remember_me.data)
        return redirect(_safe_next(request.args.get("next")) or url_for("main.home"))

    return render_template("login.html", form=form)


@bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "success")
    return redirect(url_for("main.home"))


# ---------------------------------------------------------------------------
# Authoring — admin only
# ---------------------------------------------------------------------------
@bp.route("/manage")
@admin_required
def manage():
    """The admin console: every recipe, with edit and delete on each row."""
    query = (request.args.get("q") or "").strip()
    stmt = db.select(Recipes).order_by(Recipes.title.asc())
    if query:
        like = f"%{query}%"
        stmt = stmt.where(
            db.or_(Recipes.title.ilike(like), Recipes.category.ilike(like))
        )
    items = db.session.execute(stmt).scalars().all()

    total = db.session.scalar(db.select(db.func.count()).select_from(Recipes)) or 0
    return render_template(
        "manage.html",
        recipes=items,
        total=total,
        query=query,
        delete_form=DeleteForm(),
        # Counted in Python rather than SQL: "has a photo" depends on files on
        # disk (uploads and the bundled fallbacks), which the database can't see.
        without_photo=sum(1 for r in items if not r.has_photo),
    )


def _apply(form: RecipeForm, item: Recipes) -> None:
    """Copy validated form fields onto the row, photo included.

    Returns nothing but may raise photos.PhotoError, which the callers turn into
    a field-level error rather than a 500.
    """
    item.title = form.title.data.strip()
    item.category = form.category.data.strip()
    item.prep_time = form.prep_time.data.strip()
    item.cooking_time = form.cooking_time.data.strip()
    item.yield_amount = form.yield_amount.data.strip()
    item.ingredients = form.ingredients.data
    item.instructions = form.instructions.data
    item.tips = form.tips.data or None

    upload = form.photo.data
    # A file input that was left alone arrives as None or as an empty filename;
    # neither should disturb the existing photo.
    if upload and upload.filename:
        previous = item.image_filename
        item.image_filename = photos.save(upload.read(), item.slug)
        # Only after the replacement is safely on disk, and only if the name
        # actually changed — identical bytes produce the identical filename, so
        # deleting unconditionally here would remove the photo just written.
        if previous and previous != item.image_filename:
            photos.delete(previous)
    elif form.remove_photo.data and item.image_filename:
        photos.delete(item.image_filename)
        item.image_filename = None


@bp.route("/recipes/new", methods=["GET", "POST"])
@admin_required
def create_recipe():
    form = RecipeForm()
    # Populated by the import flow below: the extraction is held in the session
    # and used to seed the form on the first GET, never written straight to the
    # database. A transcription has to be looked at before it is trusted.
    imported = None
    if request.method == "GET":
        imported = session.pop("imported_recipe", None)
        if imported:
            form = RecipeForm(data=imported["fields"])

    if form.validate_on_submit():
        item = Recipes(author_id=current_user.id)
        try:
            _apply(form, item)
        except photos.PhotoError as exc:
            form.photo.errors.append(str(exc))
            return render_template("recipe_form.html", form=form, mode="new"), 400
        db.session.add(item)
        db.session.commit()
        flash(f"“{item.title}” has been added.", "success")
        return redirect(url_for("main.recipe", recipe_id=item.id))
    return render_template("recipe_form.html", form=form, mode="new", imported=imported)


@bp.route("/recipes/<int:recipe_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_recipe(recipe_id: int):
    item = db.session.get(Recipes, recipe_id)
    if item is None:
        abort(404)

    form = RecipeForm(obj=item)
    if form.validate_on_submit():
        try:
            _apply(form, item)
        except photos.PhotoError as exc:
            form.photo.errors.append(str(exc))
            return (
                render_template(
                    "recipe_form.html", form=form, mode="edit", recipe=item,
                    delete_form=DeleteForm(),
                ),
                400,
            )
        db.session.commit()
        flash(f"“{item.title}” has been updated.", "success")
        return redirect(url_for("main.recipe", recipe_id=item.id))
    return render_template(
        "recipe_form.html", form=form, mode="edit", recipe=item,
        delete_form=DeleteForm(),
    )


@bp.route("/recipes/<int:recipe_id>/delete", methods=["POST"])
@admin_required
def delete_recipe(recipe_id: int):
    item = db.session.get(Recipes, recipe_id)
    if item is None:
        abort(404)

    form = DeleteForm()
    if not form.validate_on_submit():
        abort(400)

    title = item.title
    orphan = item.image_filename
    db.session.delete(item)
    db.session.commit()
    # Only after the row is gone, so a failed commit cannot leave a recipe
    # pointing at a photo that no longer exists.
    photos.delete(orphan)
    flash(f"“{title}” has been deleted.", "success")
    return redirect(url_for("main.manage"))


# ---------------------------------------------------------------------------
# Import a recipe from a photograph
# ---------------------------------------------------------------------------
@bp.route("/recipes/import", methods=["GET", "POST"])
@admin_required
def import_recipe():
    """Transcribe a photo of a recipe, then hand it to the form for review.

    Nothing is saved here. The extraction goes into the session and the admin is
    redirected to the normal create form with the fields pre-filled, so every
    imported recipe passes through the same validation and the same human read as
    one typed by hand.
    """
    import importer

    form = ScanForm()
    if not importer.available():
        # Configuration, not a permission problem — say which is missing.
        flash("Photo import needs ANTHROPIC_API_KEY set on the server.", "error")
        return render_template("import.html", form=form, enabled=False), 503

    if form.validate_on_submit():
        if _rate_limited("import", limit=20, window=3600):
            flash("That's a lot of imports at once. Try again in a little while.", "error")
            return render_template("import.html", form=form, enabled=True), 429

        try:
            # Normalized first: it caps the resolution (so the API bill is
            # predictable), strips EXIF, and rejects anything that isn't an
            # image before it is sent anywhere.
            image = photos.normalize(form.scan.data.read())
        except photos.PhotoError as exc:
            form.scan.errors.append(str(exc))
            return render_template("import.html", form=form, enabled=True), 400

        try:
            extracted = importer.extract(image)
        except importer.ImportError_ as exc:
            flash(str(exc), "error")
            return render_template("import.html", form=form, enabled=True), 502

        if extracted.unreadable:
            flash(
                "That photo couldn't be read reliably"
                + (f": {extracted.note}" if extracted.note else "")
                + ". Try again with more light, or straighten and crop the page.",
                "error",
            )
            return render_template("import.html", form=form, enabled=True), 422

        session["imported_recipe"] = {
            "fields": importer.to_form_data(extracted),
            "note": extracted.note,
        }
        return redirect(url_for("main.create_recipe"))

    return render_template("import.html", form=form, enabled=True)


# ---------------------------------------------------------------------------
# Operational + legacy URLs
# ---------------------------------------------------------------------------
@bp.route("/health")
def health():
    """Liveness only — deliberately does not touch the database, so nginx and
    systemd can tell 'app down' apart from 'Postgres down'."""
    from flask import jsonify

    return jsonify(status="ok", service="recipes")


@bp.route("/create_account")
def legacy_create_account():
    """Signup is gone. 302, not 301: a permanent redirect would be cached by
    browsers and proxies forever, which is the wrong promise for a decision that
    could be reversed."""
    return redirect(url_for("main.home"))


@bp.route("/create_recipe")
def legacy_create_recipe():
    return redirect(url_for("main.create_recipe"), code=301)


@bp.route("/recipes_table")
def legacy_recipes_table():
    return redirect(url_for("main.recipes_table"), code=301)


@bp.route("/<int:recipe_id>/")
def legacy_recipe(recipe_id: int):
    return redirect(url_for("main.recipe", recipe_id=recipe_id), code=301)
