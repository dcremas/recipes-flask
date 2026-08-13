"""Forms and validation.

The original accepted a one-character password, never checked that an email
looked like an email, and left duplicate username/email to blow up as an
unhandled IntegrityError from the database's unique constraint. All three are
handled here, at the form layer, where the user gets a field-level message.

The database constraints still exist and are still authoritative — routes.py
catches IntegrityError as a backstop for the race between validate and commit.
"""

from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    EmailField,
    PasswordField,
    StringField,
    SubmitField,
    TextAreaField,
)
from wtforms.validators import (
    DataRequired,
    Email,
    EqualTo,
    Length,
    Optional,
    Regexp,
    ValidationError,
)

from models import Authors

# Long enough to matter, short enough that nobody is driven to a sticky note.
MIN_PASSWORD = 10


class _HoneypotMixin:
    """Hidden field that humans never see and bots tend to fill.

    Rendered off-screen with tabindex=-1 and aria-hidden, so it costs real
    users nothing. Checked in routes.py rather than here, so a hit can be
    silently accepted rather than explained to the bot.
    """

    website = StringField("Website", validators=[Optional()])

    def trapped(self) -> bool:
        return bool((self.website.data or "").strip())


class SignupForm(FlaskForm, _HoneypotMixin):
    username = StringField(
        "Username",
        validators=[
            DataRequired(),
            Length(min=2, max=50),
            # Keeps usernames URL- and display-safe, and stops homoglyph games.
            Regexp(
                r"^[A-Za-z0-9._-]+$",
                message="Letters, numbers, dots, underscores and hyphens only.",
            ),
        ],
    )
    email = EmailField("Email", validators=[DataRequired(), Email(), Length(max=100)])
    password = PasswordField(
        "Password",
        validators=[
            DataRequired(),
            Length(
                min=MIN_PASSWORD,
                max=200,
                message=f"Use at least {MIN_PASSWORD} characters.",
            ),
        ],
    )
    password_confirm = PasswordField(
        "Confirm password",
        validators=[DataRequired(), EqualTo("password", message="Passwords must match.")],
    )
    invite_code = StringField("Invite code", validators=[Optional()])
    submit = SubmitField("Create account")

    # WTForms calls validate_<fieldname> automatically after the validator list.
    def validate_username(self, field):
        existing = Authors.query.filter(
            db_lower(Authors.username) == field.data.strip().lower()
        ).first()
        if existing:
            raise ValidationError("That username is taken.")

    def validate_email(self, field):
        existing = Authors.query.filter(
            db_lower(Authors.email) == field.data.strip().lower()
        ).first()
        if existing:
            raise ValidationError("An account with that email already exists.")


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
    submit = SubmitField("Save recipe")

    # No custom "must have content" validators here on purpose: DataRequired
    # already strips whitespace and raises StopValidation, so anything blank or
    # whitespace-only is rejected before a custom validator could run. Adding
    # one would be dead code that reads like a safeguard.


class DeleteForm(FlaskForm):
    """Bare CSRF carrier.

    Deletion is POST-only and token-checked so it cannot be triggered by a
    crawler following a link or by a cross-site form.
    """

    submit = SubmitField("Delete recipe")




def db_lower(column):
    """Case-insensitive comparison helper.

    Registration is checked case-insensitively so 'Dustin' and 'dustin' cannot
    both exist — the database's unique index is case-sensitive and would let
    them.
    """
    from sqlalchemy import func

    return func.lower(column)
