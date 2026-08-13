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
    change upstream would start raising DataError on signup.
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
    def image_file(self) -> str | None:
        """Photo for this recipe, or None when there isn't one.

        The original derived a filename from the title and rendered it
        unconditionally, so "Onion Dip" pointed at a nonexistent oniondip.jpg
        and shipped a broken image to production. Here the file is resolved
        only if it exists on disk, and templates fall back to a CSS
        placeholder when this returns None.
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

    def owned_by(self, user) -> bool:
        """True when `user` is the signed-in author of this recipe."""
        return bool(
            user
            and getattr(user, "is_authenticated", False)
            and self.author_id == user.id
        )
