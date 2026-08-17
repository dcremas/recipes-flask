"""Run one real transcription against a live API, outside the test suite.

The pytest suite deliberately never reaches a real provider, which leaves one
thing unproven: that a given model id actually exists on this account, accepts
the image, and returns something that validates against ExtractedRecipe. That is
exactly what breaks when switching providers or bumping a model, so it gets a
script rather than a leap of faith on a live deploy.

    export GEMINI_API_KEY=...
    .venv/bin/python tests/try_import.py path/to/recipe-card.jpg
    .venv/bin/python tests/try_import.py card.jpg gemini-3.7-flash gemini-2.5-flash

With no models named it tries the current default. With several, it runs each and
prints them side by side, so "which model should I pin" is a measurement rather
than an opinion. Nothing here touches the database or the site.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask  # noqa: E402

import importer  # noqa: E402
import photos  # noqa: E402


def build_app(model: str) -> Flask:
    """A bare Flask app carrying only the keys importer reads — no database, no
    routes, so this cannot touch anything real by accident."""
    app = Flask(__name__)
    app.config.update(
        IMPORT_PROVIDER=os.getenv("IMPORT_PROVIDER") or "gemini",
        GEMINI_API_KEY=(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip(),
        ANTHROPIC_API_KEY=(os.getenv("ANTHROPIC_API_KEY") or "").strip(),
        GEMINI_USE_VERTEX=os.getenv("GEMINI_USE_VERTEX", "0") not in ("0", "", "false", "False"),
        GOOGLE_CLOUD_PROJECT=(os.getenv("GOOGLE_CLOUD_PROJECT") or "").strip(),
        GOOGLE_CLOUD_LOCATION=(os.getenv("GOOGLE_CLOUD_LOCATION") or "").strip(),
        IMPORT_MODEL_GEMINI=model if model else "",
        IMPORT_MODEL_ANTHROPIC=model if model else "",
    )
    return app


def run(image_path: str, model: str) -> None:
    with open(image_path, "rb") as fh:
        raw = fh.read()

    app = build_app(model)
    with app.app_context():
        if not importer.available():
            sys.exit(f"not configured: {importer.unavailable_reason()}")
        print(f"\n=== {importer.describe().removeprefix('is enabled — ')} ===")

        # The same normalization the route applies, so the bytes on the wire and
        # the token cost are what production would actually send.
        image = photos.normalize(raw)
        print(f"image: {len(raw):,} bytes in -> {len(image):,} bytes sent")

        started = time.monotonic()
        try:
            result = importer.extract(image)
        except importer.ImportError_ as exc:
            print(f"FAILED after {time.monotonic() - started:.1f}s: {exc}")
            return
        elapsed = time.monotonic() - started

    print(f"took {elapsed:.1f}s")
    print(f"unreadable: {result.unreadable}")
    if result.note:
        print(f"note: {result.note}")
    print(f"title: {result.title!r}   category: {result.category!r}")
    print(f"times: prep={result.prep_time!r} cook={result.cooking_time!r} yield={result.yield_amount!r}")
    for label, items in (
        ("ingredients", result.ingredients),
        ("instructions", result.instructions),
        ("tips", result.tips),
    ):
        print(f"{label} ({len(items)}):")
        for item in items:
            print(f"  - {item}")


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    image_path = sys.argv[1]
    models = sys.argv[2:] or [""]  # "" means whatever the code defaults to
    for model in models:
        run(image_path, model)


if __name__ == "__main__":
    main()
