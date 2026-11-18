"""
Photo -> question text, and an AI double-check, using an OpenAI model.

* read_photo():   the model is used only as a careful *reader*: it transcribes the question and options
                  in the photo as JSON. It is never asked to answer.
* judge_match():  when the word/character matcher is not sure, a short text-only call compares the
                  photo's question with the top candidates from the bank and says whether one of them
                  is the SAME question (reworded, shuffled, cropped...). It never picks the answer
                  either: the answer always comes from the bank.
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
# Shared chat helper (adapts to whatever the model accepts)
# --------------------------------------------------------------------------- #
def _variants(model: str, schema: dict, name: str):
    """Request variants, most to least specific. A 400 (unsupported parameter) moves to the next one."""
    efforts = ["minimal", "low", None] if model.startswith(("gpt-5", "o")) else [None]
    formats = [
        {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}},
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
            raise ScanError("The reader returned something I couldn't understand. Try again.")
        try:
            return json.loads(m.group(0))
        except Exception as e:
            raise ScanError("The reader returned something I couldn't understand. Try again.") from e


def _chat_json(api_key: str, model: str, messages: list, schema: dict, name: str,
               base_url: str | None = None, max_tokens: int = 3000) -> tuple[dict, dict]:
    try:
        import openai
        from openai import OpenAI
    except ImportError as e:                           # pragma: no cover
        raise ScanError("The 'openai' package isn't installed. Add it to requirements.txt.") from e
    if not api_key:
        raise ScanError("No OpenAI API key is configured.")
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=90, max_retries=2)
    last = None
    for kw in _variants(model, schema, name):
        try:
            resp = client.chat.completions.create(model=model, messages=messages, max_completion_tokens=max_tokens, **kw)
        except openai.BadRequestError as e:            # unsupported parameter on this model -> try next variant
            last = e
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
            last = ScanError("empty reply")
            continue
        u = getattr(resp, "usage", None)
        return _parse_json(content), {"in": getattr(u, "prompt_tokens", None), "out": getattr(u, "completion_tokens", None)}
    raise ScanError(f"The model rejected the request ({last}). Try a different OPENAI_MODEL.")


# --------------------------------------------------------------------------- #
# 1) Read the photo
# --------------------------------------------------------------------------- #
SYSTEM_PROMPT = (
    "You are a meticulous OCR engine for photos of multiple-choice exam questions (AWS certification style). "
    "The photo may be blurry, skewed, dark, glare-affected, a phone photo of a screen, or cropped. "
    "Transcribe the question text and every answer option EXACTLY as printed, in the original wording.\n"
    "Rules:\n"
    "- Do NOT answer the question, do NOT explain it, and do NOT add or remove words.\n"
    "- Only repair a word when the damage is obvious and there is one clear intended word "
    "(e.g. 'Amaz0n' -> 'Amazon'); otherwise copy what you see. Use [illegible] for unreadable text.\n"
    "- Re-join words that are split by a line break or hyphen (e.g. 'Bed-' / 'rock' -> 'Bedrock').\n"
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
    data_url = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": [
            {"type": "text", "text": "Transcribe the exam question(s) in this photo as JSON."},
            {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}},
        ]},
    ]
    payload, usage = _chat_json(api_key, model, messages, SCHEMA, "exam_question", base_url)
    out = _normalise(payload)
    out["usage"] = usage
    return out


# --------------------------------------------------------------------------- #
# 2) AI double-check: is one of the top candidates the SAME question?
# --------------------------------------------------------------------------- #
JUDGE_PROMPT = (
    "You compare an exam question read from a photo (PHOTO) with candidate questions from a question bank "
    "(numbered). Decide whether one candidate is the SAME question as the photo.\n"
    "SAME means: the same scenario, the same requirement, and the same set of answer options in substance.\n"
    "Allow differences caused by OCR errors, typos, line-wrap splits, a different option order or letters, "
    "light rewording or synonyms, and missing or cropped parts of the photo.\n"
    "NOT the same: a different scenario, or a similar scenario whose options or requirements differ materially "
    "(different services, different constraints, different 'choose N').\n"
    "Do NOT decide which option is correct. Be strict: when unsure, answer 0.\n"
    "If a candidate is the same, also map each PHOTO option letter to the candidate option letter with the same "
    "meaning (omit any you are unsure of).\n"
    'Return JSON: {"match": <candidate number, or 0 for none>, "confidence": "high"|"medium"|"low", '
    '"reason": "<one short sentence>", "option_map": [{"photo": "A", "candidate": "C"}]}'
)

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "match": {"type": "integer"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "reason": {"type": "string"},
        "option_map": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"photo": {"type": "string"}, "candidate": {"type": "string"}},
                "required": ["photo", "candidate"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["match", "confidence", "reason", "option_map"],
    "additionalProperties": False,
}


def _fmt(stem: str, options: list[tuple[str, str]], limit: int = 1100) -> str:
    text = stem.strip() + "\n" + "\n".join(f"{l}. {t}" for l, t in options)
    return text if len(text) <= limit else text[:limit] + " …"


def judge_match(stem: str, options: list[tuple[str, str]], candidates: list[dict],
                api_key: str, model: str = DEFAULT_MODEL, base_url: str | None = None) -> dict:
    """candidates: [{'id': int, 'question': str, 'options': [(letter, text), ...]}, ...] (best first).

    -> {'id': int | None, 'confidence': 'high'|'medium'|'low', 'reason': str, 'to_photo': {cand_letter: photo_letter}}
    """
    cand_txt = "\n\n".join(f"[{i + 1}]\n{_fmt(c['question'], c['options'])}" for i, c in enumerate(candidates))
    messages = [
        {"role": "system", "content": JUDGE_PROMPT},
        {"role": "user", "content": f"PHOTO:\n{_fmt(stem, options, 1500)}\n\nCANDIDATES:\n{cand_txt}"},
    ]
    payload, usage = _chat_json(api_key, model, messages, JUDGE_SCHEMA, "same_question", base_url, max_tokens=1500)
    n = payload.get("match")
    chosen = candidates[n - 1]["id"] if isinstance(n, int) and 1 <= n <= len(candidates) else None
    to_photo = {}
    if chosen is not None:
        for m in payload.get("option_map") or []:
            p, c = str(m.get("photo", "")).strip().upper()[:1], str(m.get("candidate", "")).strip().upper()[:1]
            if p and c:
                to_photo[c] = p
    conf = payload.get("confidence") if payload.get("confidence") in ("high", "medium", "low") else "low"
    return {"id": chosen, "confidence": conf, "reason": str(payload.get("reason", "")).strip(),
            "to_photo": to_photo, "usage": usage}
