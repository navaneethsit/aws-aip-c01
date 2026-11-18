# AIP-C01 · Question & Answer Browser

Every question with its choices and the **correct answer(s) highlighted**, plus a search box.
No mock exam, no timer, no scoring. This is a separate copy of the question data, so the mock
app in `../aip-c01-mock` is not affected by anything here.

## Use

- **Search box (top):** type words — all words must match (question, options, answer, explanation, notes).
  Use `"quotes"` for an exact phrase, `#82` to jump to question 82, `q82` or `82` also work.
  Matches are highlighted.
- **Filters:** question type (choose one / choose two or more) and domain (multi-select).
- **Explanations:** collapsed under each answer. Turn on *Show explanations* to open them all.
  An explanation also opens by itself when your search matches text inside it.
- The search text is kept in the URL (`?q=guardrails`), so you can bookmark or share a search.

## Run locally

```powershell
cd D:\Workspace\certification\AWS\AI-Developer-Professional\aip-c01-qa-browser
py -3 -m pip install -r requirements.txt
py -3 -m streamlit run app.py
```

## Deploy on Streamlit Community Cloud

1. Push the repo that contains this folder to GitHub.
   **Use a private repository** (and restrict who can view the app in the app's *Share* settings):
   the content comes from third-party exam-dump sets, so don't publish it openly.
2. Go to <https://share.streamlit.io> → **Create app** → pick the repo and branch.
3. **Main file path:** `AWS/AI-Developer-Professional/aip-c01-qa-browser/app.py`
   (or just `app.py` if you make this folder the repo root).
4. Deploy. `requirements.txt` in this folder is picked up automatically; there are no secrets to set.

## Files

```
aip-c01-qa-browser/
├── app.py                # the whole app
├── questions.py          # question bank loader (imports the files below)
├── questions_extra*.py   # question data (Q1-Q149)
├── requirements.txt      # streamlit only
└── .streamlit/config.toml
```

To add questions later, append them to a `questions_extra*.py` file here (same fields as in the mock
app). Answers are read from each question's `answer` field, so the highlighting stays correct.
