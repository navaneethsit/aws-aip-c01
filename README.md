# AIP-C01 · Question & Answer (with photo scan)

A phone-friendly Streamlit app with two views. No mock exam, no timer, no scoring. It has its own
copy of the question data, so the mock app in `../aip-c01-mock` is not affected by anything here.

## 📷 Scan a question

Photograph a question from **any** practice set. The answer does **not** need to be shown in the photo.

1. A vision model (OpenAI) **reads** the question and its options. It is only asked to transcribe,
   never to answer.
2. The app **matches** that text against the bank. If it is a question in this bank you get the
   question, the **answer**, and which option letter that is **in your photo** (other sets often
   shuffle the options).
3. If it isn't in the bank you are told so, instead of being shown a wrong answer.

What it is built to survive: blurry, dark, skewed or low-resolution photos; rotated phone photos
(EXIF is respected); a cropped question stem or a missing option; website junk around the question;
OCR typos; and reworded questions from other sets. A match is scored; **≥ 72 % = found**,
**52–72 % = probably this one (compare the wording)**, below that = not in the bank.

If the photo is too unreadable, the app says so and shows what it read. Open
**✏️ What I read from the photo** to correct the text yourself; the match updates instantly and
doesn't call the AI again.

Tips: one question per photo · include the whole question *and* all options · avoid glare on a
screen · on a phone use **📸 Take photo / upload** (opens your camera app: best quality, back
camera). **🎥 Live camera** is the in-browser camera and needs camera permission for the site.

## 🔎 Browse & search

Every question with its choices and the correct answer(s) highlighted.

- **Search box:** whole words only (`app` never matches inside `application`); simple plurals match
  (`guardrail` = `guardrails`); filler words (a, the, to, is…) are ignored, so you can paste a
  sentence. Every remaining word must appear; results are ranked by relevance. `"quotes"` = exact
  phrase, `#82` = question number. If nothing contains every word, the closest matches are shown
  with a % match.
- **Filters:** question type and domain. Explanations are collapsed under each answer.
- The search is kept in the URL (`?q=guardrails`), so it can be bookmarked.

## Setup: the OpenAI key (only needed for 📷 Scan)

Add these in **Streamlit Cloud → your app → ⋮ → Settings → Secrets** (locally:
`.streamlit/secrets.toml`, which must never be committed to GitHub):

```toml
OPENAI_API_KEY = "sk-..."          # required for photo scanning
OPENAI_MODEL   = "gpt-5-mini"      # optional; this is the default (cheap, good at reading photos)
APP_PASSWORD   = "choose-one"      # optional; asks for this before the scanner can be used
```

- **Model:** `gpt-5-mini` is the default. For even cheaper, try `gpt-5-nano`; if a newer small
  OpenAI model is available on your account you can set it here without changing any code.
  The app adapts if a model rejects an optional parameter.
- **Cost:** roughly a fifth of a cent per photo (estimate; image ≈ 1–2k input tokens plus a short
  transcription). Results are cached per image, so re-running the same photo costs nothing.
- **Privacy:** the photo is sent to OpenAI to be read and is not stored by this app. Don't photograph
  anything you wouldn't send to OpenAI.
- **Protect your key:** a public Streamlit app lets anyone use your key. Keep the app private
  (Share settings), and/or set `APP_PASSWORD`.
- No key set? The app still works for browsing/search, and lets you paste a key for the current session.

## Run locally

```powershell
cd D:\Workspace\certification\AWS\AI-Developer-Professional\aip-c01-qa-browser
py -3 -m pip install -r requirements.txt
py -3 -m streamlit run app.py
```

To test the camera on a phone before deploying you need HTTPS (browsers block the camera on plain
http), which Streamlit Cloud provides.

## Deploy on Streamlit Community Cloud

1. Push the repo to GitHub. **Use a private repository** and restrict who can view the app (the
   content comes from third-party exam-dump sets, so don't publish it openly).
2. <https://share.streamlit.io> → **Create app** → pick the repo/branch.
3. **Main file path:** `AWS/AI-Developer-Professional/aip-c01-qa-browser/app.py`
   (or `app.py` if this folder is the repo root).
4. Add the secrets above, then deploy. `requirements.txt` is picked up automatically.

## Files

```
aip-c01-qa-browser/
├── app.py                # UI: Scan view + Browse view
├── engine.py             # search + photo-matching (no Streamlit; tested on its own)
├── scan.py               # photo preparation + OpenAI vision call
├── questions.py          # question bank loader (imports the files below)
├── questions_extra*.py   # question data (Q1-Q149)
├── requirements.txt
└── .streamlit/config.toml
```

To add questions later, append them to a `questions_extra*.py` file here. Answers come from each
question's `answer` field, so highlighting and the photo-match answer stay correct.
