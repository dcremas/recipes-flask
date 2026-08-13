"""Read a recipe out of a photograph.

Point a phone at a recipe card, a cookbook page or a printout, and this turns it
into the fields the recipe form expects. The extraction is handed back to the
admin *for review* — it is never written straight to the database, because OCR of
handwriting gets quantities wrong in ways that are invisible until you are
standing in the kitchen.

Uses Claude's vision input with a JSON schema, so the response is validated
structurally before this module ever looks at it. `messages.parse()` does that
validation at the SDK layer and retries the model on a mismatch.
"""

from __future__ import annotations

import base64

from flask import current_app
from pydantic import BaseModel, Field

# Wide enough for a dense cookbook page with thinking room on top. Opus 5 thinks
# by default and max_tokens caps thinking *plus* the response, so a value sized
# only for the JSON would truncate mid-object.
MAX_TOKENS = 8000

SYSTEM = """\
You transcribe recipes from photographs into structured fields.

Transcribe what is actually on the page. Do not improve the recipe, convert \
units, add ingredients that "should" be there, or fill gaps from your own \
knowledge of the dish — a faithful transcription with a blank field is far more \
useful than a plausible invention, because the person reviewing this can see the \
photo and cannot see what you guessed.

Specific rules:
- Ingredients and instructions: one item per list entry, in the order printed. \
Keep quantities exactly as written ("1 1/2 cups", not "1.5 cups").
- Strip list numbering and bullet characters; the site renders those itself.
- If the photo shows times or a yield, copy them. If it does not, leave those \
fields empty rather than estimating.
- category: a single short label such as Breakfast, Dessert, Italian, Soup. \
Infer it from the dish if the page does not say.
- Set unreadable to true if the photo is too blurry, cropped or dark to \
transcribe reliably, and put what is wrong in the note. Say so rather than \
producing a half-right recipe.
- note: anything the reviewer should check — a smudged quantity, a cut-off line, \
handwriting you are unsure of. Empty if the transcription is clean.\
"""


class ExtractedRecipe(BaseModel):
    """The shape the model must return. Mirrors RecipeForm's fields."""

    title: str = Field(description="The recipe's name as printed.")
    category: str = Field(description="One short category label.")
    prep_time: str = Field(default="", description="e.g. '15 minutes'. Empty if absent.")
    cooking_time: str = Field(default="", description="e.g. '1 hour'. Empty if absent.")
    yield_amount: str = Field(default="", description="e.g. '4 servings'. Empty if absent.")
    ingredients: list[str] = Field(default_factory=list, description="One per entry, in order.")
    instructions: list[str] = Field(default_factory=list, description="One step per entry, in order.")
    tips: list[str] = Field(default_factory=list, description="Notes or tips, if the page has any.")
    unreadable: bool = Field(default=False, description="True if the photo cannot be transcribed reliably.")
    note: str = Field(default="", description="What the reviewer should double-check.")


class ImportError_(RuntimeError):
    """Extraction failed. The message is user-facing."""


def available() -> bool:
    return bool(current_app.config.get("ANTHROPIC_API_KEY"))


def extract(image_bytes: bytes, media_type: str = "image/jpeg") -> ExtractedRecipe:
    """Transcribe a recipe photo into structured fields.

    `image_bytes` should already have been through photos.normalize(), which
    caps the long edge at 1600px — comfortably inside the API's limits and well
    under the per-image token cost of a full-resolution phone photo.
    """
    if not available():
        raise ImportError_("Photo import is not configured on this server.")

    # Imported lazily so the dependency is only paid for by the one route that
    # uses it, and so a missing package cannot stop the site from booting.
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - deployment problem, not a code path
        raise ImportError_("The anthropic package is not installed on this server.") from exc

    client = anthropic.Anthropic(api_key=current_app.config["ANTHROPIC_API_KEY"])

    try:
        response = client.messages.parse(
            model=current_app.config["IMPORT_MODEL"],
            max_tokens=MAX_TOKENS,
            system=SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": base64.standard_b64encode(image_bytes).decode(),
                            },
                        },
                        {"type": "text", "text": "Transcribe this recipe."},
                    ],
                }
            ],
            output_format=ExtractedRecipe,
        )
    except anthropic.APIStatusError as exc:
        current_app.logger.error("recipe import failed (%s): %s", exc.status_code, exc.message)
        if exc.status_code == 429:
            raise ImportError_("The transcription service is rate limited. Try again shortly.") from exc
        raise ImportError_("The transcription service rejected the request.") from exc
    except anthropic.APIConnectionError as exc:
        current_app.logger.error("recipe import could not reach the API: %s", exc)
        raise ImportError_("Could not reach the transcription service.") from exc

    # A refusal is a successful HTTP response with no usable content, so this has
    # to be checked before touching the parsed output.
    if response.stop_reason == "refusal":
        raise ImportError_("The transcription service declined to read that image.")
    if response.stop_reason == "max_tokens":
        raise ImportError_("That page is too long to transcribe in one pass — try photographing it in sections.")

    result = response.parsed_output
    if result is None:  # pragma: no cover - parse() raises rather than returning None
        raise ImportError_("The transcription came back in an unexpected shape.")
    return result


def to_form_data(extracted: ExtractedRecipe) -> dict[str, str]:
    """Flatten an extraction into the string fields RecipeForm binds to.

    The list fields become newline-joined text because that is exactly how the
    form and `models._lines()` represent them — one item per line.
    """
    return {
        "title": extracted.title.strip(),
        "category": extracted.category.strip(),
        "prep_time": extracted.prep_time.strip(),
        "cooking_time": extracted.cooking_time.strip(),
        "yield_amount": extracted.yield_amount.strip(),
        "ingredients": "\n".join(i.strip() for i in extracted.ingredients if i.strip()),
        "instructions": "\n".join(s.strip() for s in extracted.instructions if s.strip()),
        "tips": "\n".join(t.strip() for t in extracted.tips if t.strip()),
    }
