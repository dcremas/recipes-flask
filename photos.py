"""Photo storage for recipes.

Two rules shape everything here:

1. **Files live outside the application tree.** `UPLOAD_DIR` defaults to the
   instance folder locally and is `/var/lib/recipes/uploads` in production. It
   is not under `static/` because the systemd unit sets `ProtectHome=read-only`
   (the app physically cannot write into the app tree) and `deploy.sh` rsyncs
   with `--delete` (anything it did write would be erased by the next deploy).

2. **Nothing the client sent is trusted or kept.** Every upload is decoded by
   Pillow, downsized, stripped of metadata, and re-encoded. The bytes written to
   disk are ones this module produced, so a file that is secretly a script, a
   zip, or an EXIF payload cannot survive the round trip. The extension is never
   consulted — the decoder decides what the file is.

Filenames are content-addressed: `<slug>-<hash>.jpg`. That makes replacement
safe to cache (new bytes, new URL, no cache-busting query needed) and means two
recipes whose titles slugify identically cannot collide on one file.
"""

from __future__ import annotations

import hashlib
import io
import os

from flask import current_app

# Long edge, in pixels. The largest place a photo is displayed is the recipe
# detail hero at 560px CSS, so 1600 covers 2x displays with room to spare while
# keeping stored files small.
MAX_EDGE = 1600

# JPEG quality. 82 is the usual sweet spot where artifacts stop being visible on
# photographic content.
JPEG_QUALITY = 82

# Formats accepted from the browser. Pillow reads far more than this, but there
# is no reason to accept, say, a TIFF or an ICO as a recipe photo.
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "GIF", "HEIF", "HEIC", "MPO"}


class PhotoError(ValueError):
    """Raised when an upload is not a usable image. The message is user-facing."""


def _upload_dir() -> str:
    return current_app.config["UPLOAD_DIR"]


def normalize(data: bytes) -> bytes:
    """Decode, downsize, strip metadata, re-encode as JPEG.

    Raises PhotoError with a message worth showing to the admin.
    """
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        image = Image.open(io.BytesIO(data))
        # verify() detects truncated and malformed files, but consumes the file
        # object — so the image has to be reopened afterwards to actually use it.
        image.verify()
        image = Image.open(io.BytesIO(data))
    except UnidentifiedImageError:
        raise PhotoError("That file isn't an image we can read.") from None
    except Exception as exc:  # Pillow raises a wide variety on damaged input.
        raise PhotoError("That image appears to be damaged or incomplete.") from exc

    if image.format not in ALLOWED_FORMATS:
        raise PhotoError(f"{image.format or 'That format'} isn't supported — use JPEG, PNG or WebP.")

    # Honour the EXIF orientation tag, then drop EXIF entirely. Without this,
    # phone photos taken in portrait display sideways; keeping EXIF afterwards
    # would rotate them a second time, and would also publish GPS coordinates.
    image = ImageOps.exif_transpose(image)

    # Flatten transparency onto white. A PNG with an alpha channel cannot be
    # saved as JPEG at all, and compositing onto white matches the page.
    if image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGBA")
        canvas = Image.new("RGB", image.size, (255, 255, 255))
        canvas.paste(image, mask=image.split()[-1])
        image = canvas
    elif image.mode != "RGB":
        image = image.convert("RGB")

    image.thumbnail((MAX_EDGE, MAX_EDGE), Image.LANCZOS)

    out = io.BytesIO()
    image.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
    return out.getvalue()


def save(data: bytes, slug: str) -> str:
    """Normalize and store an upload. Returns the bare filename to persist.

    The slug is cosmetic — it makes the directory browsable by a human. The hash
    is what guarantees uniqueness, so a bad or empty slug is harmless.
    """
    processed = normalize(data)
    digest = hashlib.sha256(processed).hexdigest()[:12]
    name = f"{(slug or 'recipe')[:40]}-{digest}.jpg"

    directory = _upload_dir()
    try:
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, name)
        # Write to a temporary name and rename into place, so a crash or a
        # concurrent read never sees a half-written JPEG.
        tmp = path + ".part"
        with open(tmp, "wb") as fh:
            fh.write(processed)
        os.replace(tmp, path)
    except OSError as exc:
        current_app.logger.error("could not write upload to %s: %s", directory, exc)
        raise PhotoError("The photo could not be saved on the server.") from exc
    return name


def delete(filename: str | None) -> None:
    """Remove a stored photo. Silent when it is already gone.

    basename() is applied even though these values are written by this module:
    the value makes a round trip through the database, and a single stray path
    separator would otherwise turn this into an arbitrary-delete primitive.
    """
    if not filename:
        return
    safe = os.path.basename(filename)
    if not safe or safe in (".", ".."):
        return
    try:
        os.remove(os.path.join(_upload_dir(), safe))
    except FileNotFoundError:
        pass
    except OSError as exc:
        # Never fail a delete or an edit over a leftover file.
        current_app.logger.warning("could not remove upload %s: %s", safe, exc)
