"""
Photo -> question text, using an OpenAI vision model.

The model is used only as a careful *reader*: it transcribes the question and options in the photo
as JSON. It is never asked to answer. The answer always comes from this bank (see engine.py).
"""
from __future__ import annotations

import base64
import io
import json
import re

from PIL import Image, ImageOps

try:                                   # iPhone HEIC photos (optional)
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:                      # pragma: no cover
    pass

DEFAULT_MODEL = "gpt-5-mini"           # cheap + good vision; override with the OPENAI_MODEL secret


class ScanError(Exception):
    """A problem we can explain to the user in plain words."""


# --------------------------------------------------------------------------- #
# Image preparation: orientation, size, contrast. Never rejects an image for quality.
# --------------------------------------------------------------------------- #
def prepare_image(data: bytes, max_side: int = 2048, min_side: int = 1100) -> bytes:
    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)           # phones store rotation in EXIF
    except Exception as e:
        raise ScanError("That file isn't an image I can open. Try a JPG or PNG photo.") from e
    if img.mode in ("RGBA", "LA", "P"):               # flatten transparency on white
        bg = Image.new("RGB", img.size, (255, 255, 255))
        img = img.convert("RGBA")
        bg.paste(img, mask=img.split()[-1])
        img = bg
    else:
        img = img.convert("RGB")
    w, h = img.size
    longest = max(w, h)
    if longest > max_side:                            # keep cost and upload size sane
        k = max_side / longest
        img = img.resize((round(w * k), round(h * k)), Image.LANCZOS)
    elif longest < min_side:                          # tiny screenshots: enlarge so small text spans more pixels
        k = min_side / longest
        img = img.resize((round(w * k), round(h * k)), Image.LANCZOS)
    img = ImageOps.autocontrast(img, cutoff=1)        # helps dim / washed-out photos
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=88, optimize=True)
    return out.getvalue()


# --------------------------------------------------------------------------- #
# Vision call
# --------------------------------------------------------------------------- #
SYSTEM_PROMPT = (
    "You are a meticulous OCR engine for photos of multiple-choice exam questions (AWS certification style). "
    "The photo may be blurry, skewed, dark, glare-affected, a phone photo of a screen, or cropped. "
    "Transcribe the question text and every answer option EXACTLY as printed, in the original wording.\n"
    "Rules:\n"
    "- Do NOT answer the question, do NOT explain it, and do NOT add or remove words.\n"
    "- Only repair a word when the damage is obvious and there is one clear intended word "
    "(e.g. 'Amaz0n' -> 'Amazon'); otherwise copy what you see. Use [illegible] for unreadable text.\n"
    "- Put the question text (including any '(Choose two.)' line) in `question`, WITHOUT the options.\n"
    "- Put each option in `options` with its letter as printed (A, B, C, ...). Join text that wraps across lines.\n"
    "- Ignore website chrome: menus, ads, 'Show answer' buttons, usernames, dates, comments, page numbers.\n"
    "- If the photo shows an answer or highlights a choice, still ignore it; do not report which is correct.\n"
    "- If more than one question is visible, return each one (at most 3), the most complete one first.\n"
    "- If no question can be read at all, return an empty `questions` list and say why in `problem`.\n"
    "Return only JSON."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "options": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"letter": {"type": "string"}, "text": {"type": "string"}},
                            "required": ["letter", "text"],
                            "additionalProperties": False,
                        },
                    },
                    "readability": {"type": "string", "enum": ["clear", "partly_unclear", "very_unclear"]},
                },
                "required": ["question", "options", "readability"],
                "additionalProperties": False,
            },
        },
        "problem": {"type": "string"},
    },
    "required": ["questions", "problem"],
    "additionalProperties": False,
}


def _variants(model: str):
    """Request variants, most to least specific. A 400 (unsupported parameter) moves to the next one."""
    efforts = ["minimal", "low", None] if model.startswith(("gpt-5", "o")) else [None]
    formats = [
        {"type": "json_schema", "json_schema": {"name": "exam_question", "strict": True, "schema": SCHEMA}},
        {"type": "json_object"},
    ]
    for fmt in formats:
        for eff in efforts:
            kw = {"response_format": fmt}
            if eff:
                kw["reasoning_effort"] = eff
            yield kw


def _parse_json(text: str) -> dict:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise ScanError("The reader returned something I couldn't understand. Try the photo again.")
        return json.loads(m.group(0))


def _normalise(payload: dict) -> dict:
    qs = []
    for q in (payload.get("questions") or [])[:3]:
        stem = str(q.get("question", "")).strip()
        opts = []
        for i, o in enumerate(q.get("options") or []):
            text = str(o.get("text", "")).strip()
            if not text:
                continue
            letter = str(o.get("letter", "")).strip().upper()[:1]
            opts.append((letter if letter.isalpha() else chr(65 + i), text))
        if stem or opts:
            qs.append({"question": stem, "options": opts, "readability": q.get("readability", "clear")})
    return {"questions": qs, "problem": str(payload.get("problem", "")).strip()}


def read_photo(jpeg: bytes, api_key: str, model: str = DEFAULT_MODEL, base_url: str | None = None) -> dict:
    """Send the photo to the vision model; return {'questions': [...], 'problem': str, 'usage': {...}}."""
    try:
        import openai
        from openai import OpenAI
    except ImportError as e:                           # pragma: no cover
        raise ScanError("The 'openai' package isn't installed. Add it to requirements.txt.") from e
    if not api_key:
        raise ScanError("No OpenAI API key is configured.")

    client = OpenAI(api_key=api_key, base_url=base_url, timeout=90, max_retries=2)
    data_url = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": [
            {"type": "text", "text": "Transcribe the exam question(s) in this photo as JSON."},
            {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}},
        ]},
    ]
    last_400 = None
    for kw in _variants(model):
        try:
            resp = client.chat.completions.create(model=model, messages=messages, max_completion_tokens=3000, **kw)
        except openai.BadRequestError as e:            # unsupported parameter on this model -> try next variant
            last_400 = e
            continue
        except openai.AuthenticationError as e:
            raise ScanError("The OpenAI API key was rejected. Check the OPENAI_API_KEY secret.") from e
        except openai.NotFoundError as e:
            raise ScanError(f"The model '{model}' isn't available to this key. Set the OPENAI_MODEL secret to one "
                            "your account can use (for example gpt-5-mini or gpt-4.1-mini).") from e
        except openai.RateLimitError as e:
            raise ScanError("OpenAI says the rate limit or quota was hit. Check billing/credits and try again.") from e
        except (openai.APIConnectionError, openai.APITimeoutError) as e:
            raise ScanError("Couldn't reach OpenAI (network or timeout). Try again.") from e
        content = (resp.choices[0].message.content or "") if resp.choices else ""
        if not content.strip():                         # e.g. reasoning used the whole budget
            last_400 = ScanError("empty reply")
            continue
        out = _normalise(_parse_json(content))
        u = getattr(resp, "usage", None)
        out["usage"] = {"in": getattr(u, "prompt_tokens", None), "out": getattr(u, "completion_tokens", None)}
        return out
    raise ScanError(f"The vision model rejected the request ({last_400}). Try a different OPENAI_MODEL.")
