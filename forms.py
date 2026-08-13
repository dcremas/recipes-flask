"""Forms and validation.

There is no signup form: accounts are not self-service, so the only way one
exists is for the admin to insert it. What remains is login, the recipe form
used for both create and edit, and a bare CSRF carrier for delete.

The original recipe form's problems are still fixed here — DataRequired strips
whitespace, and lengths match the column widths in deploy/migrate.sql rather
than letting Postgres raise DataError on an over-long field.
"""

from __future__ import annotations

from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField, FileRequired
from wtforms import (
    BooleanField,
    EmailField,
    PasswordField,
    StringField,
    SubmitField,
    TextAreaField,
)
from wtforms.validators import DataRequired, Email, Length, Optional

# Checked so the browser's file picker filters sensibly and an obvious mistake is
# caught before the bytes are decoded. It is *not* the real check — photos.py
# decides what a file is by decoding it, since an extension is just a claim.
IMAGE_EXTENSIONS = ["jpg", "jpeg", "png", "webp", "gif", "heic", "heif"]
IMAGE_MESSAGE = "Choose a JPEG, PNG, WebP or HEIC image."


class LoginForm(FlaskForm):
    email = EmailField("Email", validators=[DataRequired(), Email()])
    password = PasswordField("Password", validators=[DataRequired()])
    remember_me = BooleanField("Remember me")
    submit = SubmitField("Log in")


class RecipeForm(FlaskForm):
    """Used for both create and edit — one definition, one set of rules."""

    title = StringField("Title", validators=[DataRequired(), Length(min=2, max=120)])
    category = StringField("Category", validators=[DataRequired(), Length(min=2, max=60)])
    prep_time = StringField("Prep time", validators=[DataRequired(), Length(max=60)])
    cooking_time = StringField("Cooking time", validators=[DataRequired(), Length(max=60)])
    yield_amount = StringField("Servings", validators=[DataRequired(), Length(max=60)])
    ingredients = TextAreaField(
        "Ingredients", validators=[DataRequired(), Length(max=8000)]
    )
    instructions = TextAreaField(
        "Instructions", validators=[DataRequired(), Length(max=20000)]
    )
    # Optional in the original too, but nothing downstream tolerated it being
    # blank. It genuinely is optional now.
    tips = TextAreaField("Tips", validators=[Optional(), Length(max=8000)])
    photo = FileField(
        "Photo",
        validators=[Optional(), FileAllowed(IMAGE_EXTENSIONS, IMAGE_MESSAGE)],
    )
    # Separate from "upload nothing": leaving the file input empty means "keep
    # whatever is there", so removing a photo needs its own explicit signal.
    remove_photo = BooleanField("Remove the current photo")
    submit = SubmitField("Save recipe")

    # No custom "must have content" validators here on purpose: DataRequired
    # already strips whitespace and raises StopValidation, so anything blank or
    # whitespace-only is rejected before a custom validator could run. Adding
    # one would be dead code that reads like a safeguard.


class FeedbackForm(FlaskForm):
    """The feedback form at the foot of the home page.

    Bounds match the contact form on dustincremascoli.com so the two behave
    identically. Unlike that one this is a FlaskForm, which adds a CSRF token —
    invisible to the visitor, and one less way for the endpoint to be abused
    from another origin.
    """

    name = StringField("Name", validators=[DataRequired(), Length(min=2, max=120)])
    email = EmailField("Email", validators=[DataRequired(), Email(), Length(max=200)])
    message = TextAreaField(
        "Message", validators=[DataRequired(), Length(min=10, max=5000)]
    )
    # Hidden from people, tempting to bots. Checked in the route so a hit can be
    # silently accepted rather than explained to the bot.
    website = StringField("Website", validators=[Optional()])
    submit = SubmitField("Send message")

    def trapped(self) -> bool:
        return bool((self.website.data or "").strip())


class ScanForm(FlaskForm):
    """Upload a photograph *of* a recipe, to be transcribed into the form.

    Distinct from RecipeForm.photo, which is a picture of the finished dish. This
    one is a picture of the page, and its bytes are not kept after extraction.
    """

    scan = FileField(
        "Photo of the recipe",
        validators=[
            FileRequired("Choose a photo of the recipe to import."),
            FileAllowed(IMAGE_EXTENSIONS, IMAGE_MESSAGE),
        ],
    )
    submit = SubmitField("Read the recipe")


class DeleteForm(FlaskForm):
    """Bare CSRF carrier.

    Deletion is POST-only and token-checked so it cannot be triggered by a
    crawler following a link or by a cross-site form.
    """

    submit = SubmitField("Delete recipe")
