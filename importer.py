"""Read a recipe out of a photograph.

Point a phone at a recipe card, a cookbook page or a printout, and this turns it
into the fields the recipe form expects. The extraction is handed back to the
admin *for review* — it is never written straight to the database, because OCR of
handwriting gets quantities wrong in ways that are invisible until you are
standing in the kitchen.

Either Anthropic's Claude or Google's Gemini can do the transcription. For this
job they are interchangeable — one image in, one JSON object out — so which one
runs is a deployment choice (IMPORT_PROVIDER plus whichever key is set), not a
code change. Both are driven through their native structured-output support with
the same Pydantic schema below, so the response is validated structurally before
this module ever looks at it, whichever provider produced it.

The vendor SDKs are imported inside the two extract functions rather than at
module scope, so only the provider actually in use has to be installed and a
missing package can never stop the site from booting.
"""

from __future__ import annotations

import base64

from flask import current_app
from pydantic import BaseModel, Field, ValidationError

# Wide enough for a dense cookbook page with thinking room on top. The current
# models on both providers think by default, and each provider's output cap
# counts thinking *plus* the response, so a value sized only for the JSON would
# truncate mid-object.
MAX_TOKENS = 8000

PROVIDERS = ("anthropic", "gemini")

# The credential each provider reads, the model it falls back to, and how the
# page describes where the photo is going. Kept together so adding a provider is
# one entry plus one extract function.
KEY_VARS = {"anthropic": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY"}
DEFAULT_MODELS = {"anthropic": "claude-opus-5", "gemini": "gemini-3.7-flash"}
LABELS = {"anthropic": "Anthropic's API", "gemini": "Google's Gemini API"}

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


# ---------------------------------------------------------------------------
# Which provider is in play
# ---------------------------------------------------------------------------
def _choice() -> str:
    return (current_app.config.get("IMPORT_PROVIDER") or "auto").strip().lower() or "auto"


def _configured(provider: str) -> bool:
    """True if this provider has credentials it can actually use."""
    cfg = current_app.config
    if provider == "gemini" and cfg.get("GEMINI_USE_VERTEX"):
        # Vertex authenticates with Application Default Credentials, so there is
        # no key to look for — only the project the spend lands in.
        return bool(cfg.get("GOOGLE_CLOUD_PROJECT"))
    return bool(cfg.get(KEY_VARS[provider]))


def resolve() -> str | None:
    """The provider that will run, or None when import is switched off.

    An explicit IMPORT_PROVIDER is never second-guessed: naming a provider whose
    credentials are missing turns import off and says which variable to set,
    rather than quietly falling through and billing the other account.
    """
    choice = _choice()
    if choice in PROVIDERS:
        return choice if _configured(choice) else None
    if choice != "auto":
        current_app.logger.error(
            "IMPORT_PROVIDER=%r is not a known provider — import is disabled.", choice
        )
        return None
    # 'auto' prefers Gemini, so that adding an Anthropic key later cannot silently
    # move the spend from one account to the other; switching is then a
    # deliberate IMPORT_PROVIDER edit.
    for name in ("gemini", "anthropic"):
        if _configured(name):
            return name
    return None


def available() -> bool:
    return resolve() is not None


def label() -> str:
    """How the import page names the service the photo is sent to."""
    provider = resolve()
    return LABELS[provider] if provider else "the transcription service"


def describe() -> str:
    """One line for the boot log: which account this will bill, and with what."""
    provider = resolve()
    if provider is None:
        return f"is disabled — {unavailable_reason()}"
    route = " via Vertex AI" if provider == "gemini" and current_app.config.get("GEMINI_USE_VERTEX") else ""
    return f"is enabled — {provider}{route}, model {_model_for(provider)}"


def unavailable_reason() -> str:
    """User-facing explanation for the disabled state — names the variable to set."""
    choice = _choice()
    if choice in PROVIDERS:
        return (
            f"Photo import is set to {choice} (IMPORT_PROVIDER), which needs "
            f"{KEY_VARS[choice]} set on the server."
        )
    if choice != "auto":
        return (
            f"IMPORT_PROVIDER is set to '{choice}', which is not a provider. "
            "Use 'gemini', 'anthropic' or 'auto'."
        )
    return "Photo import needs GEMINI_API_KEY or ANTHROPIC_API_KEY set on the server."


def _model_for(provider: str) -> str:
    """Per-provider, deliberately: a pin left behind from one provider must not
    break the other the moment IMPORT_PROVIDER is flipped."""
    override = (current_app.config.get(f"IMPORT_MODEL_{provider.upper()}") or "").strip()
    return override or DEFAULT_MODELS[provider]


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------
def extract(image_bytes: bytes, media_type: str = "image/jpeg") -> ExtractedRecipe:
    """Transcribe a recipe photo into structured fields.

    `image_bytes` should already have been through photos.normalize(), which
    caps the long edge at 1600px — comfortably inside both APIs' limits and well
    under the per-image token cost of a full-resolution phone photo.
    """
    provider = resolve()
    if provider is None:
        raise ImportError_("Photo import is not configured on this server.")

    model = _model_for(provider)
    if provider == "gemini":
        return _extract_gemini(image_bytes, media_type, model)
    return _extract_anthropic(image_bytes, media_type, model)


def _extract_anthropic(image_bytes: bytes, media_type: str, model: str) -> ExtractedRecipe:
    """Claude's vision input. `messages.parse()` validates the JSON against the
    schema at the SDK layer and retries the model on a mismatch."""
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - deployment problem, not a code path
        raise ImportError_("The anthropic package is not installed on this server.") from exc

    client = anthropic.Anthropic(api_key=current_app.config["ANTHROPIC_API_KEY"])

    try:
        response = client.messages.parse(
            model=model,
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
        if exc.status_code == 404:
            raise ImportError_(f"The configured model ({model}) is not available to this account.") from exc
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


# Gemini stops for a family of content-policy reasons where Claude simply says
# "refusal"; they are all the same thing to the person holding the phone.
_GEMINI_DECLINED = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "RECITATION", "SPII", "IMAGE_SAFETY"}


def _extract_gemini(image_bytes: bytes, media_type: str, model: str) -> ExtractedRecipe:
    """Gemini's vision input, with the same Pydantic model as the response schema.

    Unlike the Anthropic SDK, google-genai does not retry a schema mismatch for
    you — `response.parsed` is simply None or the wrong shape — so the validation
    is re-done here and turned into a user-facing message.
    """
    try:
        from google import genai
        from google.genai import errors, types
    except ImportError as exc:  # pragma: no cover - deployment problem, not a code path
        raise ImportError_("The google-genai package is not installed on this server.") from exc

    cfg = current_app.config
    if cfg.get("GEMINI_USE_VERTEX"):
        # Vertex mode: billed to GOOGLE_CLOUD_PROJECT through Application Default
        # Credentials, which is what makes GCP credits apply. No API key is read.
        client = genai.Client(
            vertexai=True,
            project=cfg["GOOGLE_CLOUD_PROJECT"],
            location=cfg.get("GOOGLE_CLOUD_LOCATION") or "us-central1",
        )
    else:
        client = genai.Client(api_key=cfg["GEMINI_API_KEY"])

    try:
        response = client.models.generate_content(
            model=model,
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type=media_type),
                "Transcribe this recipe.",
            ],
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM,
                max_output_tokens=MAX_TOKENS,
                response_mime_type="application/json",
                response_schema=ExtractedRecipe,
            ),
        )
    except errors.APIError as exc:
        current_app.logger.error("recipe import failed (%s): %s", exc.code, exc.message)
        if exc.code == 429:
            raise ImportError_("The transcription service is rate limited. Try again shortly.") from exc
        if exc.code == 404:
            raise ImportError_(f"The configured model ({model}) is not available to this account.") from exc
        raise ImportError_("The transcription service rejected the request.") from exc
    except Exception as exc:  # transport, DNS, ADC refresh — all "can't reach it"
        current_app.logger.error("recipe import could not reach the API: %s", exc, exc_info=True)
        raise ImportError_("Could not reach the transcription service.") from exc

    # A blocked prompt comes back as a 200 with no candidates at all, so this has
    # to be checked before indexing into them.
    blocked = getattr(getattr(response, "prompt_feedback", None), "block_reason", None)
    if blocked:
        raise ImportError_("The transcription service declined to read that image.")

    candidate = (response.candidates or [None])[0]
    reason = getattr(candidate, "finish_reason", None)
    reason = getattr(reason, "name", reason)  # FinishReason enum -> plain string
    if reason == "MAX_TOKENS":
        raise ImportError_("That page is too long to transcribe in one pass — try photographing it in sections.")
    if reason in _GEMINI_DECLINED:
        raise ImportError_("The transcription service declined to read that image.")

    result = response.parsed
    if isinstance(result, ExtractedRecipe):
        return result
    if result is None:
        raise ImportError_("The transcription came back in an unexpected shape.")
    try:
        # Gemini hands back a dict when it declines to coerce; the schema is the
        # same either way, so validate rather than throwing the transcription out.
        return ExtractedRecipe.model_validate(result)
    except ValidationError as exc:
        current_app.logger.error("recipe import returned an invalid object: %s", exc)
        raise ImportError_("The transcription came back in an unexpected shape.") from exc


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
