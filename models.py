"""Database models.

Mapped onto the existing `recipes` database (authors, recipes) that the Heroku
app used, so no data migration is involved — only the widening/NOT NULL changes
in deploy/migrate.sql, which this module assumes have been applied.

Differences from the original models.py, all deliberate:

  * Recipes no longer inherits UserMixin. It was never a user; the mixin gave
    every recipe is_authenticated/get_id(), which is meaningless and made
    `login_user(some_recipe)` type-check as valid.
  * password_hashed is 255, not 162. Werkzeug's scrypt output is exactly 162
    characters with the current defaults, i.e. zero headroom — a parameter
    change upstream would start raising DataError on any set_password().
  * joined_at no longer carries onupdate. "Joined" that moves every time the
    row is touched is simply wrong.
  * The text fields are split through _lines(), which tolerates NULL and any
    line ending. The original did `recipe.tips.split("\\r\\n")`, which 500s on
    NULL and fails to split \\n-only text seeded from SQL.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone

from flask import current_app
from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from app import db

# Anything that is not a letter, digit or space becomes nothing; spaces go away
# entirely. "Macaroni & Cheese" -> "macaronicheese", which is how the existing
# photos are named (after dropping the '&' from the legacy filename).
_SLUG_STRIP = re.compile(r"[^a-z0-9 ]+")


def slugify_title(title: str) -> str:
    if not title:
        return ""
    return _SLUG_STRIP.sub("", title.lower()).replace(" ", "")


def _lines(value: str | None) -> list[str]:
    """Split stored multi-line text into display lines.

    Normalizes CRLF/CR to LF first, then drops blank lines, so a trailing
    newline does not render an empty bullet.
    """
    if not value:
        return []
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    return [line.strip() for line in normalized.split("\n") if line.strip()]


def _utcnow() -> datetime:
    """Naive UTC, matching the existing `timestamp without time zone` columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Authors(UserMixin, db.Model):
    __tablename__ = "authors"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False, index=True)
    email = db.Column(db.String(100), unique=True, nullable=False, index=True)
    password_hashed = db.Column(db.String(255), nullable=False, default="")
    joined_at = db.Column(db.DateTime, default=_utcnow)

    recipes = db.relationship(
        "Recipes",
        backref="author",
        cascade="all, delete, delete-orphan",
        order_by="Recipes.title",
    )

    def __repr__(self) -> str:
        return f"<Author {self.username}>"

    def set_password(self, password: str) -> None:
        self.password_hashed = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        # An account with no usable hash must never authenticate. check_password_hash
        # on an empty string is falsy anyway, but being explicit keeps it obvious.
        if not self.password_hashed:
            return False
        return check_password_hash(self.password_hashed, password)

    @property
    def is_admin(self) -> bool:
        """True for the single account named by ADMIN_EMAIL.

        Config rather than a column because the app connects as `recipes_app`,
        which holds DML rights only — an is_admin column would need a migration
        run as the database owner. Comparison is lowercase on both sides since
        login already normalizes the address it looks up.
        """
        admin = (current_app.config.get("ADMIN_EMAIL") or "").strip().lower()
        if not admin or not self.email:
            return False
        return self.email.strip().lower() == admin


class Recipes(db.Model):
    __tablename__ = "recipes"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    author_id = db.Column(
        db.Integer, db.ForeignKey("authors.id"), nullable=False, index=True
    )
    category = db.Column(db.String(60), nullable=False)
    title = db.Column(db.String(120), nullable=False)
    prep_time = db.Column(db.String)
    cooking_time = db.Column(db.String)
    yield_amount = db.Column(db.String)
    ingredients = db.Column(db.String)
    instructions = db.Column(db.String)
    tips = db.Column(db.String)
    # Bare filename inside UPLOAD_DIR, never a path. NULL means "no upload",
    # in which case the bundled static/img/recipes photo is used if one exists.
    image_filename = db.Column(db.String(255))
    timestamp = db.Column(db.DateTime, default=_utcnow, onupdate=_utcnow)

    def __repr__(self) -> str:
        return f"<Recipe {self.id} {self.title!r}>"

    # -- display helpers ---------------------------------------------------
    @property
    def ingredient_list(self) -> list[str]:
        return _lines(self.ingredients)

    @property
    def instruction_list(self) -> list[str]:
        return _lines(self.instructions)

    @property
    def tip_list(self) -> list[str]:
        return _lines(self.tips)

    @property
    def slug(self) -> str:
        return slugify_title(self.title)

    @property
    def bundled_image(self) -> str | None:
        """Legacy slug-derived photo shipped under static/img/recipes, or None.

        The original derived this filename from the title and rendered it
        unconditionally, so "Onion Dip" pointed at a nonexistent oniondip.jpg
        and shipped a broken image to production. The file is resolved only if
        it actually exists on disk.

        Still consulted so every recipe that had a photo before uploads existed
        keeps it — but an upload always wins, and new photos never land here
        (this directory is inside the rsync'd, read-only app tree).
        """
        slug = self.slug
        if not slug:
            return None
        root = os.path.join(current_app.static_folder, "img", "recipes")
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            candidate = slug + ext
            if os.path.isfile(os.path.join(root, candidate)):
                return f"img/recipes/{candidate}"
        return None

    @property
    def photo_path(self) -> str | None:
        """Absolute filesystem path to this recipe's photo, or None.

        Used by the PDF renderer, which needs a file:// path rather than a URL
        so rendering never depends on the site being reachable from itself.
        """
        if self.image_filename:
            candidate = os.path.join(
                current_app.config["UPLOAD_DIR"], self.image_filename
            )
            if os.path.isfile(candidate):
                return candidate
            # Row points at a file that is gone — fall through to the bundled
            # photo rather than rendering a broken image.
        bundled = self.bundled_image
        if bundled:
            return os.path.join(current_app.static_folder, *bundled.split("/")[1:])
        return None

    @property
    def photo_url(self) -> str | None:
        """URL for this recipe's photo, or None when it has none.

        Uploads are content-addressed (the filename carries a hash of the
        bytes), so they need no cache-busting query and a replacement photo is
        a different URL. The bundled fallback still goes through static_v.
        """
        from flask import url_for

        if self.image_filename:
            if os.path.isfile(
                os.path.join(current_app.config["UPLOAD_DIR"], self.image_filename)
            ):
                return url_for("main.media", filename=self.image_filename)
        bundled = self.bundled_image
        if bundled:
            return url_for(
                "static", filename=bundled, v=current_app.config.get("ASSET_V", "1")
            )
        return None

    @property
    def has_photo(self) -> bool:
        return self.photo_url is not None
