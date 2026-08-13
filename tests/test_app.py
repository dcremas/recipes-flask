"""Behavioral tests against a real Postgres clone of the production database.

Each named bug below exists in the Heroku version; the test asserts the new
behavior. Run with:

    DATABASE_URL=postgresql+psycopg:///recipes_test .venv/bin/python -m pytest -q
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app, db  # noqa: E402
from models import Authors, Recipes, _lines, slugify_title  # noqa: E402


@pytest.fixture()
def app():
    application = create_app(
        {
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "SESSION_COOKIE_SECURE": False,
            "ASSET_V": "t",
        }
    )
    yield application


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def user(app):
    """A throwaway author, removed afterwards along with anything they wrote."""
    with app.app_context():
        a = Authors(username="pytest_user", email="pytest@example.com")
        a.set_password("correct horse battery")
        db.session.add(a)
        db.session.commit()
        uid = a.id
    yield uid
    with app.app_context():
        a = db.session.get(Authors, uid)
        if a:
            db.session.delete(a)
            db.session.commit()


def login(client, email="pytest@example.com", password="correct horse battery"):
    return client.post(
        "/login", data={"email": email, "password": password}, follow_redirects=True
    )


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------
class TestLineSplitting:
    """BUG: `recipe.tips.split("\\r\\n")` 500s on NULL and mis-splits \\n text."""

    def test_none_is_empty_not_crash(self):
        assert _lines(None) == []

    def test_empty_string(self):
        assert _lines("") == []

    def test_crlf(self):
        assert _lines("a\r\nb") == ["a", "b"]

    def test_bare_lf_still_splits(self):
        # SQL-seeded rows use \n; the original returned one unsplit blob.
        assert _lines("a\nb\nc") == ["a", "b", "c"]

    def test_bare_cr(self):
        assert _lines("a\rb") == ["a", "b"]

    def test_blank_lines_dropped(self):
        assert _lines("a\n\n\nb\n") == ["a", "b"]


class TestSlug:
    def test_ampersand_dropped(self):
        assert slugify_title("Macaroni & Cheese") == "macaronicheese"

    def test_spaces_and_case(self):
        assert slugify_title("Homemade Pancakes") == "homemadepancakes"

    def test_empty(self):
        assert slugify_title("") == ""


# --------------------------------------------------------------------------
# Public pages
# --------------------------------------------------------------------------
class TestPublicPages:
    @pytest.mark.parametrize("path", ["/", "/home", "/recipes", "/recipes/table"])
    def test_ok(self, client, path):
        assert client.get(path).status_code == 200

    def test_every_recipe_renders(self, client, app):
        """BUG: 'Onion Dip' had no image file and shipped a broken <img>."""
        with app.app_context():
            ids = [r.id for r in db.session.execute(db.select(Recipes)).scalars()]
        assert ids, "fixture database should contain recipes"
        for rid in ids:
            resp = client.get(f"/recipes/{rid}")
            assert resp.status_code == 200, f"recipe {rid} failed to render"

    def test_missing_photo_renders_placeholder_not_broken_img(self, client, app):
        with app.app_context():
            onion = db.session.execute(
                db.select(Recipes).where(Recipes.title.ilike("%onion%"))
            ).scalar_one_or_none()
            if onion is None:
                pytest.skip("no photo-less recipe in fixture data")
            rid, slug = onion.id, onion.slug
        html = client.get(f"/recipes/{rid}").get_data(as_text=True)
        assert f"{slug}.jpg" not in html, "must not reference a nonexistent image"
        assert "img-placeholder" in html

    def test_unknown_recipe_404s(self, client):
        assert client.get("/recipes/99999").status_code == 404

    def test_category_filter(self, client):
        assert client.get("/recipes?category=Mexican").status_code == 200


class TestLegacyUrls:
    """Old Heroku URLs must keep working."""

    @pytest.mark.parametrize(
        "old,new",
        [
            ("/create_account", "/register"),
            ("/create_recipe", "/recipes/new"),
            ("/recipes_table", "/recipes/table"),
            ("/1/", "/recipes/1"),
        ],
    )
    def test_redirects(self, client, old, new):
        resp = client.get(old)
        assert resp.status_code == 301
        assert resp.headers["Location"].endswith(new)


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------
class TestAuth:
    def test_login_rejects_wrong_password(self, client, user):
        resp = client.post(
            "/login", data={"email": "pytest@example.com", "password": "wrong"}
        )
        assert resp.status_code == 401

    def test_login_succeeds(self, client, user):
        assert b"pytest_user" in login(client).data

    def test_login_is_case_insensitive_on_email(self, client, user):
        resp = client.post(
            "/login",
            data={"email": "PyTest@Example.COM", "password": "correct horse battery"},
            follow_redirects=True,
        )
        assert b"pytest_user" in resp.data

    def test_short_password_rejected(self, client):
        """BUG: the original accepted a one-character password."""
        resp = client.post(
            "/register",
            data={
                "username": "shortpw",
                "email": "shortpw@example.com",
                "password": "a",
                "password_confirm": "a",
            },
        )
        assert resp.status_code == 200  # redisplayed with errors
        assert b"at least 10 characters" in resp.data.lower()

    def test_bad_email_rejected(self, client):
        resp = client.post(
            "/register",
            data={
                "username": "bademail",
                "email": "not-an-email",
                "password": "a-long-enough-password",
                "password_confirm": "a-long-enough-password",
            },
        )
        assert resp.status_code == 200
        assert b"bademail" not in resp.data or b"email" in resp.data.lower()

    def test_duplicate_email_is_a_form_error_not_a_500(self, client, user):
        """BUG: duplicates surfaced as an unhandled IntegrityError."""
        resp = client.post(
            "/register",
            data={
                "username": "someone_else",
                "email": "pytest@example.com",
                "password": "a-long-enough-password",
                "password_confirm": "a-long-enough-password",
            },
        )
        assert resp.status_code == 200
        assert b"already exists" in resp.data

    def test_honeypot_creates_no_account(self, client, app):
        client.post(
            "/register",
            data={
                "username": "spambot",
                "email": "spam@example.com",
                "password": "a-long-enough-password",
                "password_confirm": "a-long-enough-password",
                "website": "http://spam.example",
            },
        )
        with app.app_context():
            assert (
                db.session.execute(
                    db.select(Authors).where(Authors.username == "spambot")
                ).scalar_one_or_none()
                is None
            )

    def test_open_redirect_blocked(self, client, user):
        resp = client.post(
            "/login?next=https://evil.example",
            data={"email": "pytest@example.com", "password": "correct horse battery"},
        )
        assert "evil.example" not in resp.headers.get("Location", "")


# --------------------------------------------------------------------------
# Authoring + ownership
# --------------------------------------------------------------------------
class TestAuthoring:
    def test_create_requires_login(self, client):
        resp = client.get("/recipes/new")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_create_then_edit_then_delete(self, client, user, app):
        login(client)
        resp = client.post(
            "/recipes/new",
            data={
                "title": "Test Soup",
                "category": "Testing",
                "prep_time": "5 minutes",
                "cooking_time": "10 minutes",
                "yield_amount": "2 servings",
                "ingredients": "water\nsalt",
                "instructions": "boil water\nadd salt",
                "tips": "",  # blank on purpose: the original crashed on this
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            item = db.session.execute(
                db.select(Recipes).where(Recipes.title == "Test Soup")
            ).scalar_one()
            rid = item.id
            assert item.tips is None
            assert item.ingredient_list == ["water", "salt"]

        # A recipe with no tips must render, not 500.
        assert client.get(f"/recipes/{rid}").status_code == 200

        client.post(
            f"/recipes/{rid}/edit",
            data={
                "title": "Test Stew",
                "category": "Testing",
                "prep_time": "5 minutes",
                "cooking_time": "20 minutes",
                "yield_amount": "2 servings",
                "ingredients": "water\nsalt\npepper",
                "instructions": "boil water\nadd salt",
                "tips": "stir often",
            },
            follow_redirects=True,
        )
        with app.app_context():
            assert db.session.get(Recipes, rid).title == "Test Stew"

        client.post(f"/recipes/{rid}/delete", follow_redirects=True)
        with app.app_context():
            assert db.session.get(Recipes, rid) is None

    def test_cannot_edit_someone_elses_recipe(self, client, user, app):
        with app.app_context():
            other = db.session.execute(
                db.select(Recipes).where(Recipes.author_id != user)
            ).scalars().first()
            assert other is not None, "fixture needs a recipe by another author"
            rid = other.id
        login(client)
        assert client.get(f"/recipes/{rid}/edit").status_code == 403
        assert client.post(f"/recipes/{rid}/delete").status_code == 403

    def test_delete_rejects_get(self, client, user, app):
        with app.app_context():
            rid = db.session.execute(db.select(Recipes)).scalars().first().id
        login(client)
        # GET on delete must not be routable — a crawler must not destroy data.
        assert client.get(f"/recipes/{rid}/delete").status_code == 405

    def test_whitespace_only_ingredients_rejected(self, client, user, app):
        """DataRequired strips, so whitespace-only never reaches the database."""
        login(client)
        resp = client.post(
            "/recipes/new",
            data={
                "title": "Empty",
                "category": "Testing",
                "prep_time": "1 minute",
                "cooking_time": "1 minute",
                "yield_amount": "1",
                "ingredients": "   \n  \n",
                "instructions": "do nothing",
                "tips": "",
            },
        )
        # Form redisplayed with a field error rather than redirecting.
        assert resp.status_code == 200
        assert b"field-error" in resp.data
        with app.app_context():
            assert (
                db.session.execute(
                    db.select(Recipes).where(Recipes.title == "Empty")
                ).scalar_one_or_none()
                is None
            )


class TestOps:
    def test_health(self, client):
        assert client.get("/health").get_json()["status"] == "ok"

    def test_404_page(self, client):
        resp = client.get("/definitely-not-a-page")
        assert resp.status_code == 404
        assert b"couldn" in resp.data


class TestPdf:
    """The PDF is the point of the site — it must always be one readable page."""

    @staticmethod
    def _pages(data: bytes) -> int:
        """Page count via a real parser.

        Grepping the bytes for "/Type /Page" does not work: WeasyPrint emits
        PDF 1.7 with compressed object streams, so the page objects are inside
        a Flate stream and never appear in the raw output.
        """
        import io

        from pypdf import PdfReader

        return len(PdfReader(io.BytesIO(data)).pages)

    def test_pdf_renders(self, client, app):
        with app.app_context():
            rid = db.session.execute(db.select(Recipes)).scalars().first().id
        resp = client.get(f"/recipes/{rid}.pdf")
        assert resp.status_code == 200
        assert resp.mimetype == "application/pdf"
        assert resp.data[:5] == b"%PDF-"

    def test_pdf_is_inline_with_a_sensible_filename(self, client, app):
        with app.app_context():
            r = db.session.execute(db.select(Recipes)).scalars().first()
            rid, slug = r.id, r.slug
        cd = client.get(f"/recipes/{rid}.pdf").headers["Content-Disposition"]
        assert cd.startswith("inline")
        assert f'filename="{slug}.pdf"' in cd

    def test_every_recipe_is_exactly_one_page(self, client, app):
        with app.app_context():
            ids = [r.id for r in db.session.execute(db.select(Recipes)).scalars()]
        for rid in ids:
            data = client.get(f"/recipes/{rid}.pdf").data
            assert self._pages(data) == 1, f"recipe {rid} produced multiple pages"

    def test_long_recipe_still_fits_one_page(self, client, app, user):
        """Exercises the auto-fit ladder, which real recipes never reach."""
        with app.app_context():
            r = Recipes(
                author_id=user,
                category="Testing",
                title="Fit Ladder",
                prep_time="10 Minutes",
                cooking_time="20 Minutes",
                yield_amount="4",
                ingredients="\n".join(
                    f"{i} cup of a fairly long ingredient name here" for i in range(40)
                ),
                instructions="\n".join(
                    f"Step {i}: do a wordy thing that wraps onto a second line."
                    for i in range(30)
                ),
                tips="A tip.",
            )
            db.session.add(r)
            db.session.commit()
            rid = r.id
        try:
            assert self._pages(client.get(f"/recipes/{rid}.pdf").data) == 1
        finally:
            with app.app_context():
                db.session.delete(db.session.get(Recipes, rid))
                db.session.commit()

    def test_missing_recipe_pdf_404s(self, client):
        assert client.get("/recipes/99999.pdf").status_code == 404

    def test_photoless_recipe_pdf_renders(self, client, app):
        """A recipe with no photo must still produce a valid PDF."""
        with app.app_context():
            r = db.session.execute(
                db.select(Recipes).where(Recipes.title.ilike("%onion%"))
            ).scalar_one_or_none()
            if r is None:
                pytest.skip("no photo-less recipe in fixture data")
            rid = r.id
        resp = client.get(f"/recipes/{rid}.pdf")
        assert resp.status_code == 200
        assert self._pages(resp.data) == 1
