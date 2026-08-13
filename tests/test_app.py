"""Behavioral tests against a real Postgres clone of the production database.

Each named bug below exists in the Heroku version; the test asserts the new
behavior. Run with:

    DATABASE_URL=postgresql+psycopg:///recipes_test .venv/bin/python -m pytest -q
"""

import io
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import photos  # noqa: E402
from app import create_app, db  # noqa: E402
from models import Authors, Recipes, _lines, slugify_title  # noqa: E402


def make_image(size=(1200, 900), fmt="JPEG", color=(200, 120, 60)):
    """An actual encoded image, not a stub.

    photos.normalize() decodes what it is given, so a fixture of fake bytes
    would only ever exercise the rejection path.
    """
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format=fmt)
    return buf.getvalue()


@pytest.fixture()
def app(tmp_path):
    application = create_app(
        {
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "SESSION_COOKIE_SECURE": False,
            "ASSET_V": "t",
            # The admin is whoever holds this address, so the `user` fixture
            # below is the admin and `other_user` deliberately is not.
            "ADMIN_EMAIL": "pytest@example.com",
            # Per-test upload directory: uploads must never touch the real one,
            # and tmp_path gives each test a clean slate.
            "UPLOAD_DIR": str(tmp_path / "uploads"),
            # Import is off unless a test turns it on, so no test can reach the
            # real API by accident.
            "ANTHROPIC_API_KEY": "",
        }
    )
    os.makedirs(application.config["UPLOAD_DIR"], exist_ok=True)
    yield application


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """Clear the login limiter between tests.

    `routes._ATTEMPTS` is process-global and keyed on remote address, which is
    the same for every test client — so once eight tests have signed in, the
    ninth gets a 429 and its failure reads like a broken auth gate rather than
    leaked state.
    """
    import routes

    routes._ATTEMPTS.clear()
    yield


PASSWORD = "correct horse battery"


def _author(app, username, email):
    with app.app_context():
        a = Authors(username=username, email=email)
        a.set_password(PASSWORD)
        db.session.add(a)
        db.session.commit()
        uid = a.id
    yield uid
    with app.app_context():
        a = db.session.get(Authors, uid)
        if a:
            db.session.delete(a)
            db.session.commit()


@pytest.fixture()
def user(app):
    """The admin, removed afterwards along with anything they wrote."""
    yield from _author(app, "pytest_user", "pytest@example.com")


@pytest.fixture()
def other_user(app):
    """A signed-in account that is *not* the admin — the case the gate exists for."""
    yield from _author(app, "pytest_other", "other@example.com")


def login(client, email="pytest@example.com", password=PASSWORD):
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
            ("/create_recipe", "/recipes/new"),
            ("/recipes_table", "/recipes/table"),
            ("/1/", "/recipes/1"),
        ],
    )
    def test_redirects(self, client, old, new):
        resp = client.get(old)
        assert resp.status_code == 301
        assert resp.headers["Location"].endswith(new)

    def test_create_account_goes_home_not_to_a_signup_form(self, client):
        """Signup is gone, but the address is still linked from the old site."""
        resp = client.get("/create_account")
        assert resp.status_code == 302
        # url_for("main.home") may build either of the endpoint's two rules.
        assert resp.headers["Location"] in ("/", "/home")

    def test_register_is_no_longer_routable(self, client):
        for method in (client.get, client.post):
            assert method("/register").status_code == 404


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
            data={"email": "PyTest@Example.COM", "password": PASSWORD},
            follow_redirects=True,
        )
        assert b"pytest_user" in resp.data

    def test_open_redirect_blocked(self, client, user):
        resp = client.post(
            "/login?next=https://evil.example",
            data={"email": "pytest@example.com", "password": PASSWORD},
        )
        assert "evil.example" not in resp.headers.get("Location", "")

    def test_no_signup_invitation_anywhere_public(self, client):
        """The site must not offer visitors an account it cannot create."""
        for path in ("/", "/recipes", "/recipes/table", "/login"):
            html = client.get(path).get_data(as_text=True).lower()
            assert "/register" not in html, f"{path} still links to signup"
            assert "create an account" not in html, f"{path} still invites signup"
            assert "create account" not in html, f"{path} still invites signup"

    def test_login_is_not_advertised_in_the_nav(self, client):
        """Reachable, but unlinked — nothing on the public site points at it."""
        assert client.get("/login").status_code == 200
        assert ">Log in<" not in client.get("/").get_data(as_text=True)


class TestAdminFlag:
    def test_admin_email_matches_case_insensitively(self, app):
        with app.app_context():
            a = Authors(username="x", email="PyTest@Example.com")
            assert a.is_admin is True

    def test_other_accounts_are_not_admin(self, app):
        with app.app_context():
            assert Authors(username="y", email="other@example.com").is_admin is False

    def test_unset_admin_email_makes_nobody_admin(self, app):
        """Fail closed: a missing setting must not promote everyone."""
        with app.app_context():
            app.config["ADMIN_EMAIL"] = ""
            assert Authors(username="z", email="pytest@example.com").is_admin is False


# --------------------------------------------------------------------------
# Authoring — admin only
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

    def test_admin_may_edit_a_recipe_written_by_another_author(self, client, user, app):
        """Authoring is no longer author-scoped: the admin curates everything."""
        with app.app_context():
            other = (
                db.session.execute(db.select(Recipes).where(Recipes.author_id != user))
                .scalars()
                .first()
            )
            assert other is not None, "fixture needs a recipe by another author"
            rid = other.id
        login(client)
        assert client.get(f"/recipes/{rid}/edit").status_code == 200

    def test_signed_in_non_admin_cannot_author_anything(
        self, client, other_user, app
    ):
        """The whole point of the change — enforced server-side, not by hiding
        buttons. A GET on /recipes/new is the tell: if the gate were only in the
        template, this would render the form."""
        with app.app_context():
            rid = db.session.execute(db.select(Recipes)).scalars().first().id
        login(client, email="other@example.com")
        assert client.get("/recipes/new").status_code == 403
        assert client.post("/recipes/new", data={"title": "Nope"}).status_code == 403
        assert client.get(f"/recipes/{rid}/edit").status_code == 403
        assert client.post(f"/recipes/{rid}/delete").status_code == 403

    def test_recipe_page_hides_authoring_controls_from_visitors(self, client, app):
        with app.app_context():
            rid = db.session.execute(db.select(Recipes)).scalars().first().id
        html = client.get(f"/recipes/{rid}").get_data(as_text=True)
        assert "/edit" not in html
        assert "/delete" not in html
        # The public affordance that must survive.
        assert f"/recipes/{rid}.pdf" in html

    def test_recipe_page_shows_authoring_controls_to_admin(self, client, user, app):
        with app.app_context():
            rid = db.session.execute(db.select(Recipes)).scalars().first().id
        login(client)
        html = client.get(f"/recipes/{rid}").get_data(as_text=True)
        assert f"/recipes/{rid}/edit" in html
        assert f"/recipes/{rid}/delete" in html

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


# --------------------------------------------------------------------------
# Photo uploads
# --------------------------------------------------------------------------
class TestPhotoNormalization:
    """photos.normalize() is the security boundary: the extension is a claim,
    the decoder is the authority."""

    def test_rejects_a_non_image(self, app):
        with app.app_context():
            with pytest.raises(photos.PhotoError):
                photos.normalize(b"#!/bin/sh\necho not an image\n")

    def test_rejects_a_script_disguised_as_a_jpeg(self, app):
        """A .jpg extension must not get a file past the decoder."""
        with app.app_context():
            with pytest.raises(photos.PhotoError):
                photos.normalize(b"\xff\xd8\xff\xe0 GIF89a <?php system($_GET[0]); ?>")

    def test_reencodes_to_jpeg(self, app):
        with app.app_context():
            out = photos.normalize(make_image(fmt="PNG"))
        assert out[:3] == b"\xff\xd8\xff", "output must be JPEG regardless of input format"

    def test_downsizes_to_the_long_edge_cap(self, app):
        from PIL import Image

        with app.app_context():
            out = photos.normalize(make_image(size=(4000, 3000)))
        assert max(Image.open(io.BytesIO(out)).size) == photos.MAX_EDGE

    def test_small_images_are_not_upscaled(self, app):
        from PIL import Image

        with app.app_context():
            out = photos.normalize(make_image(size=(300, 200)))
        assert Image.open(io.BytesIO(out)).size == (300, 200)

    def test_transparency_is_flattened_not_fatal(self, app):
        """An RGBA PNG cannot be saved as JPEG at all without compositing."""
        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGBA", (400, 300), (10, 20, 30, 0)).save(buf, format="PNG")
        with app.app_context():
            out = photos.normalize(buf.getvalue())
        assert Image.open(io.BytesIO(out)).mode == "RGB"

    def test_filenames_are_content_addressed(self, app):
        """Same bytes -> same name (so a re-save is idempotent); different bytes
        -> different name (so a replacement is a new, cacheable URL)."""
        with app.app_context():
            a = photos.save(make_image(color=(1, 2, 3)), "soup")
            b = photos.save(make_image(color=(1, 2, 3)), "soup")
            c = photos.save(make_image(color=(9, 8, 7)), "soup")
        assert a == b
        assert a != c

    def test_delete_ignores_a_traversal_attempt(self, app, tmp_path):
        """The stored value round-trips through the database; a stray separator
        must not turn delete() into an arbitrary-unlink primitive."""
        victim = tmp_path / "important.txt"
        victim.write_text("do not delete me")
        with app.app_context():
            photos.delete("../important.txt")
        assert victim.exists()

    def test_delete_is_silent_when_already_gone(self, app):
        with app.app_context():
            photos.delete("nope-000000000000.jpg")  # must not raise


class TestPhotoUploadFlow:
    def test_upload_then_serve_then_remove(self, client, user, app):
        login(client)
        resp = client.post(
            "/recipes/new",
            data={
                "title": "Photo Soup",
                "category": "Testing",
                "prep_time": "5 minutes",
                "cooking_time": "10 minutes",
                "yield_amount": "2 servings",
                "ingredients": "water\nsalt",
                "instructions": "boil\nserve",
                "tips": "",
                "photo": (io.BytesIO(make_image()), "dinner.png"),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert resp.status_code == 200

        with app.app_context():
            item = db.session.execute(
                db.select(Recipes).where(Recipes.title == "Photo Soup")
            ).scalar_one()
            rid, stored = item.id, item.image_filename
            assert stored and stored.endswith(".jpg")
            assert os.path.isfile(os.path.join(app.config["UPLOAD_DIR"], stored))

        try:
            # The photo must actually be reachable over HTTP.
            served = client.get(f"/media/{stored}")
            assert served.status_code == 200
            assert served.data[:3] == b"\xff\xd8\xff"
            # ...and referenced by the recipe page.
            assert stored in client.get(f"/recipes/{rid}").get_data(as_text=True)

            # Removing it clears the column and the file.
            client.post(
                f"/recipes/{rid}/edit",
                data={
                    "title": "Photo Soup",
                    "category": "Testing",
                    "prep_time": "5 minutes",
                    "cooking_time": "10 minutes",
                    "yield_amount": "2 servings",
                    "ingredients": "water\nsalt",
                    "instructions": "boil\nserve",
                    "tips": "",
                    "remove_photo": "y",
                },
                content_type="multipart/form-data",
                follow_redirects=True,
            )
            with app.app_context():
                assert db.session.get(Recipes, rid).image_filename is None
            assert not os.path.isfile(os.path.join(app.config["UPLOAD_DIR"], stored))
        finally:
            with app.app_context():
                item = db.session.get(Recipes, rid)
                if item:
                    db.session.delete(item)
                    db.session.commit()

    def test_editing_without_touching_the_file_input_keeps_the_photo(
        self, client, user, app
    ):
        """The commonest edit is a text fix. An empty file input means 'leave it
        alone', not 'delete it'."""
        login(client)
        with app.app_context():
            item = Recipes(
                author_id=user, title="Keeper", category="Testing",
                ingredients="a", instructions="b",
                image_filename=photos.save(make_image(), "keeper"),
            )
            db.session.add(item)
            db.session.commit()
            rid, stored = item.id, item.image_filename
        try:
            client.post(
                f"/recipes/{rid}/edit",
                data={
                    "title": "Keeper Renamed", "category": "Testing",
                    "prep_time": "1 minute", "cooking_time": "1 minute",
                    "yield_amount": "1", "ingredients": "a",
                    "instructions": "b", "tips": "",
                },
                content_type="multipart/form-data",
                follow_redirects=True,
            )
            with app.app_context():
                after = db.session.get(Recipes, rid)
                assert after.title == "Keeper Renamed"
                assert after.image_filename == stored
            assert os.path.isfile(os.path.join(app.config["UPLOAD_DIR"], stored))
        finally:
            with app.app_context():
                item = db.session.get(Recipes, rid)
                if item:
                    db.session.delete(item)
                    db.session.commit()

    def test_replacing_a_photo_removes_the_old_file(self, client, user, app):
        login(client)
        with app.app_context():
            item = Recipes(
                author_id=user, title="Swap", category="Testing",
                ingredients="a", instructions="b",
                image_filename=photos.save(make_image(color=(5, 5, 5)), "swap"),
            )
            db.session.add(item)
            db.session.commit()
            rid, original = item.id, item.image_filename
        try:
            client.post(
                f"/recipes/{rid}/edit",
                data={
                    "title": "Swap", "category": "Testing",
                    "prep_time": "1 minute", "cooking_time": "1 minute",
                    "yield_amount": "1", "ingredients": "a",
                    "instructions": "b", "tips": "",
                    "photo": (io.BytesIO(make_image(color=(200, 30, 30))), "new.jpg"),
                },
                content_type="multipart/form-data",
                follow_redirects=True,
            )
            with app.app_context():
                replacement = db.session.get(Recipes, rid).image_filename
            assert replacement != original
            up = app.config["UPLOAD_DIR"]
            assert os.path.isfile(os.path.join(up, replacement))
            assert not os.path.isfile(os.path.join(up, original)), "old file leaked"
        finally:
            with app.app_context():
                item = db.session.get(Recipes, rid)
                if item:
                    db.session.delete(item)
                    db.session.commit()

    def test_a_bad_upload_is_a_field_error_not_a_500(self, client, user, app):
        login(client)
        resp = client.post(
            "/recipes/new",
            data={
                "title": "Bad Photo", "category": "Testing",
                "prep_time": "1 minute", "cooking_time": "1 minute",
                "yield_amount": "1", "ingredients": "a",
                "instructions": "b", "tips": "",
                "photo": (io.BytesIO(b"not an image at all"), "lies.jpg"),
            },
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400
        assert b"field-error" in resp.data
        with app.app_context():
            assert (
                db.session.execute(
                    db.select(Recipes).where(Recipes.title == "Bad Photo")
                ).scalar_one_or_none()
                is None
            ), "no row should be created when the photo is rejected"

    def test_deleting_a_recipe_removes_its_photo(self, client, user, app):
        login(client)
        with app.app_context():
            item = Recipes(
                author_id=user, title="Doomed", category="Testing",
                ingredients="a", instructions="b",
                image_filename=photos.save(make_image(color=(3, 3, 3)), "doomed"),
            )
            db.session.add(item)
            db.session.commit()
            rid, stored = item.id, item.image_filename
        client.post(f"/recipes/{rid}/delete", follow_redirects=True)
        with app.app_context():
            assert db.session.get(Recipes, rid) is None
        assert not os.path.isfile(os.path.join(app.config["UPLOAD_DIR"], stored))

    def test_a_missing_file_falls_back_instead_of_breaking(self, client, user, app):
        """A row pointing at a deleted file must render the placeholder, not a
        broken image — the same defect the original had with slug-derived names."""
        with app.app_context():
            item = Recipes(
                author_id=user, title="Ghost Photo", category="Testing",
                ingredients="a", instructions="b",
                image_filename="does-not-exist-000000000000.jpg",
            )
            db.session.add(item)
            db.session.commit()
            rid = item.id
            assert item.photo_url is None
            assert item.photo_path is None
        try:
            html = client.get(f"/recipes/{rid}").get_data(as_text=True)
            assert "does-not-exist" not in html
            assert "img-placeholder" in html
        finally:
            with app.app_context():
                db.session.delete(db.session.get(Recipes, rid))
                db.session.commit()


# --------------------------------------------------------------------------
# Admin console
# --------------------------------------------------------------------------
class TestManageConsole:
    def test_requires_admin(self, client, other_user):
        assert client.get("/manage").status_code == 302  # anonymous -> login
        login(client, email="other@example.com")
        assert client.get("/manage").status_code == 403

    def test_lists_recipes_with_edit_and_delete(self, client, user, app):
        login(client)
        with app.app_context():
            rid = db.session.execute(db.select(Recipes)).scalars().first().id
        html = client.get("/manage").get_data(as_text=True)
        assert f"/recipes/{rid}/edit" in html
        assert f"/recipes/{rid}/delete" in html

    def test_search_filters_by_title(self, client, user, app):
        login(client)
        with app.app_context():
            item = db.session.execute(db.select(Recipes)).scalars().first()
            title = item.title
        html = client.get("/manage", query_string={"q": title[:6]}).get_data(as_text=True)
        assert title in html
        assert client.get("/manage", query_string={"q": "zzzznomatch"}).status_code == 200

    def test_manage_is_not_indexable(self, client, user):
        login(client)
        assert b"noindex" in client.get("/manage").data


# --------------------------------------------------------------------------
# Import a recipe from a photo
# --------------------------------------------------------------------------
class TestImport:
    def test_requires_admin(self, client, other_user):
        assert client.get("/recipes/import").status_code == 302
        login(client, email="other@example.com")
        assert client.get("/recipes/import").status_code == 403

    def test_disabled_without_an_api_key(self, client, user):
        """No key configured must be an explained 503, not a traceback."""
        login(client)
        resp = client.get("/recipes/import")
        assert resp.status_code == 503
        assert b"ANTHROPIC_API_KEY" in resp.data

    def test_hidden_from_the_console_when_disabled(self, client, user):
        login(client)
        assert b"/recipes/import" not in client.get("/manage").data

    def test_offered_in_the_console_when_enabled(self, client, user, app):
        app.config["ANTHROPIC_API_KEY"] = "sk-ant-test"
        login(client)
        assert b"/recipes/import" in client.get("/manage").data

    def test_extraction_seeds_the_form_and_saves_nothing(
        self, client, user, app, monkeypatch
    ):
        """The whole point of the flow: a transcription is a draft for review,
        never a write. Nothing may reach the database before the admin submits."""
        import importer

        app.config["ANTHROPIC_API_KEY"] = "sk-ant-test"
        monkeypatch.setattr(
            importer,
            "extract",
            lambda *a, **k: importer.ExtractedRecipe(
                title="Grandma's Cornbread",
                category="Bread",
                prep_time="10 minutes",
                cooking_time="25 minutes",
                yield_amount="8 pieces",
                ingredients=["1 cup cornmeal", "1 tsp salt"],
                instructions=["Heat the oven to 400F.", "Mix and bake."],
                tips=["Use a cast iron pan."],
                note="The sugar quantity is smudged.",
            ),
        )
        login(client)
        resp = client.post(
            "/recipes/import",
            data={"scan": (io.BytesIO(make_image()), "card.jpg")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/recipes/new")

        with app.app_context():
            assert (
                db.session.execute(
                    db.select(Recipes).where(Recipes.title == "Grandma's Cornbread")
                ).scalar_one_or_none()
                is None
            ), "import must not write to the database"

        html = client.get("/recipes/new").get_data(as_text=True)
        assert "Grandma&#39;s Cornbread" in html or "Grandma's Cornbread" in html
        assert "1 cup cornmeal" in html
        assert "Heat the oven to 400F." in html
        # The model's own caveat has to reach the person who can check it.
        assert "smudged" in html
        assert "check it before saving" in html.lower()

    def test_seeded_form_is_consumed_once(self, client, user, app, monkeypatch):
        """A refresh of /recipes/new must not resurrect a discarded draft."""
        import importer

        app.config["ANTHROPIC_API_KEY"] = "sk-ant-test"
        monkeypatch.setattr(
            importer,
            "extract",
            lambda *a, **k: importer.ExtractedRecipe(
                title="One Shot", category="Testing", ingredients=["x"],
                instructions=["y"],
            ),
        )
        login(client)
        client.post(
            "/recipes/import",
            data={"scan": (io.BytesIO(make_image()), "card.jpg")},
            content_type="multipart/form-data",
        )
        assert b"One Shot" in client.get("/recipes/new").data
        assert b"One Shot" not in client.get("/recipes/new").data

    def test_unreadable_photo_is_explained_not_saved(
        self, client, user, app, monkeypatch
    ):
        import importer

        app.config["ANTHROPIC_API_KEY"] = "sk-ant-test"
        monkeypatch.setattr(
            importer,
            "extract",
            lambda *a, **k: importer.ExtractedRecipe(
                title="", category="", unreadable=True, note="too dark to read",
            ),
        )
        login(client)
        resp = client.post(
            "/recipes/import",
            data={"scan": (io.BytesIO(make_image()), "blurry.jpg")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 422
        assert b"too dark to read" in resp.data

    def test_api_failure_is_a_message_not_a_500(self, client, user, app, monkeypatch):
        import importer

        app.config["ANTHROPIC_API_KEY"] = "sk-ant-test"

        def boom(*a, **k):
            raise importer.ImportError_("Could not reach the transcription service.")

        monkeypatch.setattr(importer, "extract", boom)
        login(client)
        resp = client.post(
            "/recipes/import",
            data={"scan": (io.BytesIO(make_image()), "card.jpg")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 502
        assert b"Could not reach" in resp.data

    def test_a_non_image_is_rejected_before_any_api_call(
        self, client, user, app, monkeypatch
    ):
        """Never spend an API call on something that isn't an image."""
        import importer

        app.config["ANTHROPIC_API_KEY"] = "sk-ant-test"
        calls = []
        monkeypatch.setattr(importer, "extract", lambda *a, **k: calls.append(1))
        login(client)
        resp = client.post(
            "/recipes/import",
            data={"scan": (io.BytesIO(b"definitely not an image"), "lies.jpg")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400
        assert calls == [], "extract() must not be reached for a non-image"

    def test_to_form_data_flattens_lists_to_lines(self):
        import importer

        out = importer.to_form_data(
            importer.ExtractedRecipe(
                title=" Soup ", category=" Lunch ",
                ingredients=["water", "  ", "salt"],
                instructions=["boil", "serve"],
            )
        )
        assert out["title"] == "Soup"
        assert out["ingredients"] == "water\nsalt", "blank entries must be dropped"
        assert out["instructions"] == "boil\nserve"
        assert out["tips"] == ""
