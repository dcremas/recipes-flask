"""Recipes — a family recipe collection.

Rebuilt from the Heroku app (recipes-heroku) for self-hosting on EC2 behind
nginx, and since narrowed to a single-author site: the collection is public to
read and print, and only the admin can add, edit or delete.

Structure mirrors prosite_flask: an application factory, content in content.py,
models/forms/routes in their own modules. Run with gunicorn in production; the
`flask run` path is for local work only.
"""

from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timezone

from dotenv import load_dotenv
from flask import Flask, render_template
from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy
from werkzeug.middleware.proxy_fix import ProxyFix

load_dotenv()

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "main.login"
login_manager.login_message = "Please log in to continue."
login_manager.login_message_category = "info"


def _database_uri() -> str:
    """Resolve the connection string.

    DATABASE_URL wins so the systemd unit can inject it. The legacy app used
    EXTERNAL_URL, so that is still honored to keep an old .env working. Heroku's
    'postgres://' scheme is normalized because SQLAlchemy 2 only accepts
    'postgresql://'.
    """
    uri = os.getenv("DATABASE_URL") or os.getenv("EXTERNAL_URL") or ""
    if uri.startswith("postgres://"):
        uri = uri.replace("postgres://", "postgresql://", 1)
    return uri


def _adopt_gunicorn_logging(app: Flask) -> None:
    """Make the factory's own log lines visible in production.

    Under gunicorn nothing ever attaches a handler to Flask's `app.logger`, so
    records fall through to logging's handler of last resort — which writes to
    stderr but *only* at WARNING and above. Every `app.logger.info` the factory
    emits is therefore silently dropped in production while appearing fine
    locally, which is how the boot summary below came to be unreadable on the
    one box where it matters. Borrowing gunicorn's handlers and level fixes it
    without configuring logging twice; when gunicorn is not the host (tests,
    `flask run`) there are no handlers to borrow and this does nothing.
    """
    gunicorn_logger = logging.getLogger("gunicorn.error")
    if gunicorn_logger.handlers:
        app.logger.handlers = gunicorn_logger.handlers
        app.logger.setLevel(gunicorn_logger.level)


def create_app(config: dict | None = None) -> Flask:
    app = Flask(__name__)
    # First, so that every warning and summary below is actually emitted.
    _adopt_gunicorn_logging(app)

    secret = os.getenv("SECRET_KEY")
    if not secret:
        # Never crash a running site over a missing key, but never silently
        # accept a predictable one either: a random key invalidates existing
        # sessions on restart, which is visible enough to get noticed.
        if os.getenv("FLASK_ENV") == "production":
            raise RuntimeError("SECRET_KEY must be set in production")
        secret = secrets.token_hex(32)
        app.logger.warning("SECRET_KEY unset — using a random key; sessions reset on restart.")

    app.config.update(
        SECRET_KEY=secret,
        SQLALCHEMY_DATABASE_URI=_database_uri(),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        # Recycle before Postgres' idle timeout and check liveness on checkout;
        # without this a restarted database leaves the pool full of dead
        # connections and every request 500s until the worker is bounced.
        SQLALCHEMY_ENGINE_OPTIONS={
            "pool_pre_ping": True,
            "pool_recycle": 280,
        },
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        # nginx terminates TLS; mark cookies secure only when that is true.
        SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "1") != "0",
        # 1 MB was fine for text-only forms; a phone photo of a recipe card is
        # routinely 4-8 MB. The upload path re-encodes and downsizes, so this
        # only bounds what the socket will accept.
        MAX_CONTENT_LENGTH=12 * 1024 * 1024,
        # The one account allowed to add, edit or delete. Compared lowercase
        # against Authors.email; see Authors.is_admin.
        ADMIN_EMAIL=(os.getenv("ADMIN_EMAIL") or "").strip().lower(),
        # Uploaded photos live OUTSIDE the application tree, deliberately:
        #   * the systemd unit sets ProtectHome=read-only, so the app cannot
        #     write anywhere under /home/ec2-user/recipes_flask at all; and
        #   * deploy.sh rsyncs with --delete, so anything it could write under
        #     static/ would be erased by the next deploy.
        # Both failure modes are silent, which is why this is not a static/ path.
        UPLOAD_DIR=os.getenv("UPLOAD_DIR") or "",
        # --- Recipe-from-photo transcription -------------------------------
        # Anthropic and Gemini are interchangeable for this job, so the provider
        # is configuration. 'auto' uses whichever key is present, preferring
        # Gemini; naming one explicitly pins it. See importer.resolve().
        IMPORT_PROVIDER=(os.getenv("IMPORT_PROVIDER") or "auto").strip().lower(),
        ANTHROPIC_API_KEY=(os.getenv("ANTHROPIC_API_KEY") or "").strip(),
        # GOOGLE_API_KEY is the other name Google's own SDK reads, accepted here
        # so a key pasted under either name works.
        GEMINI_API_KEY=(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip(),
        # Optional model pins, per provider so one cannot break the other when
        # IMPORT_PROVIDER is flipped. IMPORT_MODEL is what the Anthropic-only
        # version of this app used, so it is still honored rather than ignored.
        IMPORT_MODEL_ANTHROPIC=(os.getenv("IMPORT_MODEL_ANTHROPIC") or os.getenv("IMPORT_MODEL") or "").strip(),
        IMPORT_MODEL_GEMINI=(os.getenv("IMPORT_MODEL_GEMINI") or "").strip(),
        # Bill Gemini through Vertex AI on Application Default Credentials rather
        # than an AI Studio key. This is the path GCP credits actually apply to.
        GEMINI_USE_VERTEX=os.getenv("GEMINI_USE_VERTEX", "0") not in ("0", "", "false", "False"),
        GOOGLE_CLOUD_PROJECT=(os.getenv("GOOGLE_CLOUD_PROJECT") or "").strip(),
        GOOGLE_CLOUD_LOCATION=(os.getenv("GOOGLE_CLOUD_LOCATION") or "").strip(),
        # Feedback form. Same variable names as the main site so both are
        # operated identically; delivery order is SES -> SMTP -> file.
        FEEDBACK_ENABLED=os.getenv("FEEDBACK_ENABLED", "1") not in ("0", "false", "False"),
        MAIL_BACKEND=(os.getenv("MAIL_BACKEND") or "auto").strip().lower(),
        MAIL_FROM=os.getenv("MAIL_FROM"),
        MAIL_TO=os.getenv("MAIL_TO") or "dustincremascoli@gmail.com",
        # Same name and default as the main site. us-east-2 is where the
        # instance and the verified SES identity actually live.
        SES_REGION=os.getenv("SES_REGION", "us-east-2"),
        SMTP_HOST=os.getenv("SMTP_HOST"),
        SMTP_PORT=int(os.getenv("SMTP_PORT", "587")),
        SMTP_USER=os.getenv("SMTP_USER"),
        SMTP_PASSWORD=os.getenv("SMTP_PASSWORD"),
        SMTP_FROM=os.getenv("SMTP_FROM"),
        MESSAGES_FILE=os.getenv("MESSAGES_FILE") or "",
    )
    if config:
        app.config.update(config)

    if not app.config["ADMIN_EMAIL"]:
        # Fail closed rather than loud: with no admin nobody can author, but the
        # public site — which is the whole point — keeps serving. Warning only,
        # because taking the site down over this would be the worse outcome.
        app.logger.warning("ADMIN_EMAIL unset — authoring is disabled for every account.")

    # Default the upload directory to the instance folder so `flask run` works
    # with no configuration; production points it at /var/lib/recipes/uploads.
    if not app.config["UPLOAD_DIR"]:
        app.config["UPLOAD_DIR"] = os.path.join(app.instance_path, "uploads")
    try:
        os.makedirs(app.config["UPLOAD_DIR"], exist_ok=True)
    except OSError as exc:
        # Don't refuse to boot: the public site does not need to write photos.
        # Uploads will fail loudly at the point of use instead.
        app.logger.error("UPLOAD_DIR %s is not usable: %s", app.config["UPLOAD_DIR"], exc)

    # Say which account the transcription bill will land on, at boot, in the
    # journal — the alternative is finding out from a statement.
    import importer

    with app.app_context():
        if importer.available():
            app.logger.info("'import from photo' %s", importer.describe())
        else:
            app.logger.warning("'import from photo' is disabled — %s", importer.unavailable_reason())

    # Feedback fallback file. Must be writable by the service: the unit sets
    # ProtectSystem=strict, so this belongs under a ReadWritePaths entry, not in
    # the app tree. Defaults beside the uploads for exactly that reason.
    if not app.config["MESSAGES_FILE"]:
        app.config["MESSAGES_FILE"] = os.path.join(
            os.path.dirname(app.config["UPLOAD_DIR"].rstrip(os.sep)) or app.instance_path,
            "feedback.jsonl",
        )
    if app.config["FEEDBACK_ENABLED"] and not app.config["MAIL_FROM"]:
        # Not fatal — messages still land in MESSAGES_FILE, and the page says
        # "recorded" rather than "sent" so nobody is misled.
        app.logger.warning(
            "MAIL_FROM unset — feedback will be written to %s instead of emailed.",
            app.config["MESSAGES_FILE"],
        )

    if not app.config["SQLALCHEMY_DATABASE_URI"]:
        raise RuntimeError("DATABASE_URL (or EXTERNAL_URL) must be set")

    # One proxy hop: nginx. Without this, url_for(_external=True) and the
    # remote address used for rate limiting both see the proxy, not the client.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    db.init_app(app)
    login_manager.init_app(app)

    from routes import bp

    app.register_blueprint(bp)

    _register_filters(app)
    _register_errors(app)
    return app


def _register_filters(app: Flask) -> None:
    from content import FEEDBACK as CONTENT_FEEDBACK
    from content import SITE

    @app.context_processor
    def inject_globals():
        import importer
        from flask_login import current_user

        return {
            "site": SITE,
            "year": datetime.now(timezone.utc).year,
            # Every authoring control in the templates keys off this one flag, so
            # there is a single place to get it wrong. getattr keeps it False for
            # anonymous visitors, whose proxy has no is_admin at all.
            "is_admin": bool(getattr(current_user, "is_admin", False)),
            # Resolved per request, not cached at boot, so flipping the provider
            # or a key in config is reflected without a code path of its own.
            "import_enabled": importer.available(),
            "import_provider_label": importer.label(),
            "feedback": CONTENT_FEEDBACK,
            "feedback_enabled": bool(app.config.get("FEEDBACK_ENABLED")),
        }

    @app.template_filter("static_v")
    def static_v(path: str) -> str:
        """Cache-busting static URL, matching prosite_flask's convention."""
        from flask import url_for

        return url_for("static", filename=path, v=app.config.get("ASSET_V", "1"))


def _register_errors(app: Flask) -> None:
    @app.errorhandler(403)
    def forbidden(_e):
        return render_template(
            "error.html", code=403,
            message="That page isn't available to your account.",
        ), 403

    @app.errorhandler(404)
    def not_found(_e):
        return render_template(
            "error.html", code=404,
            message="We couldn't find that page.",
        ), 404

    @app.errorhandler(413)
    def too_large(_e):
        return render_template(
            "error.html", code=413,
            message="That submission was too large.",
        ), 413

    @app.errorhandler(500)
    def server_error(_e):
        # The session may hold a failed transaction; leaving it dirty makes
        # every subsequent request on this worker fail too.
        db.session.rollback()
        return render_template(
            "error.html", code=500,
            message="Something went wrong on our end.",
        ), 500


# Asset version stamp: mtime of the stylesheet, so a CSS change busts caches.
def _asset_version(app: Flask) -> str:
    try:
        return str(int(os.path.getmtime(os.path.join(app.static_folder, "css/site.css"))))
    except OSError:
        return "1"


app = create_app()
app.config["ASSET_V"] = _asset_version(app)
