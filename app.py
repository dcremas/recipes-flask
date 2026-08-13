"""Recipes — a family recipe collection.

Rebuilt from the Heroku app (recipes-heroku) for self-hosting on EC2 behind
nginx, and since narrowed to a single-author site: the collection is public to
read and print, and only the admin can add, edit or delete.

Structure mirrors prosite_flask: an application factory, content in content.py,
models/forms/routes in their own modules. Run with gunicorn in production; the
`flask run` path is for local work only.
"""

from __future__ import annotations

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


def create_app(config: dict | None = None) -> Flask:
    app = Flask(__name__)

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
        ANTHROPIC_API_KEY=(os.getenv("ANTHROPIC_API_KEY") or "").strip(),
        IMPORT_MODEL=os.getenv("IMPORT_MODEL") or "claude-opus-5",
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

    if not app.config["ANTHROPIC_API_KEY"]:
        app.logger.warning("ANTHROPIC_API_KEY unset — 'import from photo' is disabled.")

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
    from content import SITE

    @app.context_processor
    def inject_globals():
        from flask_login import current_user

        return {
            "site": SITE,
            "year": datetime.now(timezone.utc).year,
            # Every authoring control in the templates keys off this one flag, so
            # there is a single place to get it wrong. getattr keeps it False for
            # anonymous visitors, whose proxy has no is_admin at all.
            "is_admin": bool(getattr(current_user, "is_admin", False)),
            "import_enabled": bool(app.config.get("ANTHROPIC_API_KEY")),
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
