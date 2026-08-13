"""Routes.

Every URL the Heroku app served still resolves — the renamed ones via 301, so
existing links and bookmarks keep working:

    /create_account  -> /register
    /create_recipe   -> /recipes/new
    /recipes_table   -> /recipes/table
    /<id>/           -> /recipes/<id>

New in this version: edit and delete, both author-scoped and enforced on the
server, not merely hidden in the template.
"""

from __future__ import annotations

import os
import time
from collections import defaultdict, deque
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
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy.exc import IntegrityError

from app import db, login_manager
from content import FEATURES, HERO
from forms import DeleteForm, LoginForm, RecipeForm, SignupForm
from models import Authors, Recipes

bp = Blueprint("main", __name__)


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
    if item.image_file:
        photo_path = os.path.join(static_dir, *item.image_file.split("/"))
        if os.path.isfile(photo_path):
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


@bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("main.home"))
    if not current_app.config["REGISTRATION_OPEN"]:
        abort(404)

    form = SignupForm()
    if form.validate_on_submit():
        # Bots that fill the hidden field get a success page and no account.
        # Telling them they failed only teaches them to stop filling it.
        if form.trapped():
            flash("Your account has been created. You can now log in.", "success")
            return redirect(url_for("main.login"))

        if _rate_limited("register", limit=5, window=3600):
            flash("Too many sign-ups from this address. Try again later.", "error")
            return render_template("register.html", form=form), 429

        author = Authors(
            username=form.username.data.strip(),
            email=form.email.data.strip().lower(),
        )
        author.set_password(form.password.data)
        db.session.add(author)
        try:
            db.session.commit()
        except IntegrityError:
            # Backstop for the gap between the form's uniqueness check and this
            # commit. The database constraint is the real authority.
            db.session.rollback()
            flash("That username or email is already registered.", "error")
            return render_template("register.html", form=form), 409

        flash("Your account has been created. You can now log in.", "success")
        return redirect(url_for("main.login"))

    return render_template("register.html", form=form)


# ---------------------------------------------------------------------------
# Authoring
# ---------------------------------------------------------------------------
def _apply(form: RecipeForm, item: Recipes) -> None:
    item.title = form.title.data.strip()
    item.category = form.category.data.strip()
    item.prep_time = form.prep_time.data.strip()
    item.cooking_time = form.cooking_time.data.strip()
    item.yield_amount = form.yield_amount.data.strip()
    item.ingredients = form.ingredients.data
    item.instructions = form.instructions.data
    item.tips = form.tips.data or None


@bp.route("/recipes/new", methods=["GET", "POST"])
@login_required
def create_recipe():
    form = RecipeForm()
    if form.validate_on_submit():
        item = Recipes(author_id=current_user.id)
        _apply(form, item)
        db.session.add(item)
        db.session.commit()
        flash(f"“{item.title}” has been added.", "success")
        return redirect(url_for("main.recipe", recipe_id=item.id))
    return render_template("recipe_form.html", form=form, mode="new")


@bp.route("/recipes/<int:recipe_id>/edit", methods=["GET", "POST"])
@login_required
def edit_recipe(recipe_id: int):
    item = db.session.get(Recipes, recipe_id)
    if item is None:
        abort(404)
    # Ownership is enforced here, not by hiding the button in the template.
    if not item.owned_by(current_user):
        abort(403)

    form = RecipeForm(obj=item)
    if form.validate_on_submit():
        _apply(form, item)
        db.session.commit()
        flash(f"“{item.title}” has been updated.", "success")
        return redirect(url_for("main.recipe", recipe_id=item.id))
    return render_template("recipe_form.html", form=form, mode="edit", recipe=item)


@bp.route("/recipes/<int:recipe_id>/delete", methods=["POST"])
@login_required
def delete_recipe(recipe_id: int):
    item = db.session.get(Recipes, recipe_id)
    if item is None:
        abort(404)
    if not item.owned_by(current_user):
        abort(403)

    form = DeleteForm()
    if not form.validate_on_submit():
        abort(400)

    title = item.title
    db.session.delete(item)
    db.session.commit()
    flash(f"“{title}” has been deleted.", "success")
    return redirect(url_for("main.recipes"))


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
    return redirect(url_for("main.register"), code=301)


@bp.route("/create_recipe")
def legacy_create_recipe():
    return redirect(url_for("main.create_recipe"), code=301)


@bp.route("/recipes_table")
def legacy_recipes_table():
    return redirect(url_for("main.recipes_table"), code=301)


@bp.route("/<int:recipe_id>/")
def legacy_recipe(recipe_id: int):
    return redirect(url_for("main.recipe", recipe_id=recipe_id), code=301)
