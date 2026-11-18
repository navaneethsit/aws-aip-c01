"""
AIP-C01 Question & Answer Browser
  * 📷 Scan: photograph a question from ANY practice set (answers not needed); a vision model reads it,
    and if it is in this bank you get the question and the answer.
  * 🔎 Browse: every question with its choices and correct answer, plus whole-word search.
No mock exam, no timer, no scoring. Works on a phone.

Run locally:   streamlit run app.py
Deploy:        Streamlit Community Cloud (see README.md)
"""
import hashlib
import hmac
import html
import os
import re

import streamlit as st

import scan
from engine import PROBABLE, SURE, Index, band
from questions import QUESTIONS

st.set_page_config(page_title="AIP-C01 Q&A", page_icon="🔎", layout="wide", initial_sidebar_state="collapsed")

st.markdown(
    """
    <style>
      .block-container {padding-top: 2rem; max-width: 1050px;}
      .qa-card{border:1px solid rgba(128,128,128,.28);border-radius:12px;padding:16px 18px;margin:0 0 14px;
        background:rgba(128,128,128,.05);}
      .qa-meta{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px;}
      .qa-badge{font-size:.74rem;font-weight:600;padding:2px 9px;border-radius:999px;background:rgba(128,128,128,.18);}
      .qa-badge.id{background:rgba(255,153,0,.22);}
      .qa-badge.multi{background:rgba(255,200,0,.22);}
      .qa-badge.pct{background:rgba(46,160,67,.25);}
      .qa-q p{margin:0 0 8px;}
      .qa-opts{list-style:none;margin:12px 0 0;padding:0;display:grid;gap:7px;}
      .qa-opt{display:flex;gap:10px;padding:9px 12px;border:1px solid rgba(128,128,128,.25);border-radius:9px;}
      .qa-opt .l{font-weight:700;min-width:1.3em;opacity:.7;}
      .qa-opt.ok{background:rgba(46,160,67,.16);border-color:rgba(46,160,67,.55);}
      .qa-opt.ok .l{opacity:1;color:#2ea043;}
      .qa-opt .tick{margin-left:auto;font-weight:700;color:#2ea043;white-space:nowrap;align-self:flex-start;}
      .qa-ans{margin-top:12px;font-weight:700;color:#2ea043;}
      details.qa-ex{margin-top:12px;border-top:1px dashed rgba(128,128,128,.4);padding-top:10px;}
      details.qa-ex>summary{cursor:pointer;font-weight:600;font-size:.9rem;opacity:.85;}
      .qa-exb{margin-top:8px;font-size:.94rem;}
      .qa-exb p{margin:0 0 8px;} .qa-exb ul{margin:0 0 8px 1.1em;padding:0;}
      .qa-note{margin-top:10px;padding:8px 12px;border-radius:9px;font-size:.88rem;
        background:rgba(255,200,0,.14);border:1px solid rgba(255,200,0,.5);}
      .qa-miss{margin-top:8px;font-size:.82rem;opacity:.75;}
      .qa-foot{margin-top:10px;display:flex;gap:14px;flex-wrap:wrap;font-size:.8rem;opacity:.75;}
      .qa-foot a{color:inherit;}
      mark{background:rgba(255,213,0,.55);color:inherit;border-radius:3px;padding:0 1px;}
      .scan-ans{margin:10px 0 14px;padding:14px 16px;border-radius:12px;font-size:1.25rem;font-weight:700;
        background:rgba(46,160,67,.16);border:2px solid rgba(46,160,67,.6);}
      .scan-ans small{display:block;margin-top:4px;font-size:.85rem;font-weight:500;opacity:.85;}
      @media (max-width: 640px){
        .block-container{padding:1rem .7rem 3rem;}
        .qa-card{padding:12px;border-radius:10px;}
        .qa-opt{padding:8px 9px;gap:8px;font-size:.95rem;}
        .qa-opt .tick{display:none;}      /* the green background already marks the answer */
        .scan-ans{font-size:1.1rem;padding:12px;}
      }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_resource
def get_index() -> Index:
    return Index(QUESTIONS)


IDX = get_index()
DOMAINS = list(dict.fromkeys(q["category"] for q in IDX.items))
SCAN_VIEW, BROWSE_VIEW = "📷 Scan a question", "🔎 Browse & search"


def secret(name: str, default=None):
    try:
        v = st.secrets.get(name)
    except Exception:
        v = None
    return v or os.environ.get(name, default)


# --------------------------------------------------------------------------- #
# Rendering (each card is one line of HTML: no blank lines, so markdown leaves it alone)
# --------------------------------------------------------------------------- #
def esc(s: str) -> str:
    return html.escape(s).replace("$", "&#36;")      # $ would otherwise start LaTeX in st.markdown


def fin(s: str) -> str:
    return s.replace("\x02", "<mark>").replace("\x03", "</mark>")


def make_marker(words, phrases):
    pats = [r"(?<![A-Za-z0-9])" + r"[^A-Za-z0-9]+".join(re.escape(w) for w in p.split()) + r"(?![A-Za-z0-9])" for p in phrases]
    pats += [r"(?<![A-Za-z0-9])" + re.escape(s) + r"(?:es|s)?(?![A-Za-z0-9])" for _, s in words]
    if not pats:
        return lambda text: text
    rx = re.compile("|".join(pats), re.I)
    return lambda text: rx.sub(lambda m: "\x02" + m.group(0) + "\x03", text)


def md_lite(text: str, mk) -> str:
    out, ul = [], False
    for raw in esc(mk(text)).split("\n"):
        line = raw.strip()
        if not line:
            if ul:
                out.append("</ul>"); ul = False
            continue
        line = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line)
        line = re.sub(r"`([^`]+)`", r"<code>\1</code>", line)
        if line.startswith("- "):
            if not ul:
                out.append("<ul>"); ul = True
            out.append(f"<li>{line[2:]}</li>")
        else:
            if ul:
                out.append("</ul>"); ul = False
            out.append(f"<p>{line}</p>")
    if ul:
        out.append("</ul>")
    return fin("".join(out))


def card(q: dict, mk=lambda t: t, expand=False, words=(), phrases=(), pct: float | None = None, miss=()) -> str:
    paras = "".join(f"<p>{fin(esc(mk(p)))}</p>" for p in q["question"].split("\n") if p.strip())
    opts = "".join(
        f'<li class="qa-opt{" ok" if o["ok"] else ""}"><span class="l">{o["letter"]}.</span>'
        f'<span>{fin(esc(mk(o["text"])))}</span>{"<span class=tick>✓ correct</span>" if o["ok"] else ""}</li>'
        for o in q["opts"]
    )
    # open the explanation by itself only when the hit is *only* in the explanation/note
    in_main = any(s in q["q_s"] or s in q["o_s"] for _, s in words) or any(p in q["main_norm"] for p in phrases)
    in_expl = any(s in q["x_s"] for _, s in words) or any(p in q["all_norm"] and p not in q["main_norm"] for p in phrases)
    open_attr = " open" if (expand or (in_expl and not in_main)) else ""
    votes = ""
    if q.get("votes"):
        votes = "<span>Community vote: " + " · ".join(f"{esc(str(k))} {v}%" for k, v in q["votes"].items()) + "</span>"
    ref = (f'<a href="{html.escape(q["reference"])}" target="_blank" rel="noopener noreferrer">AWS / source doc ↗</a>'
           if q.get("reference") else "")
    note = f'<div class="qa-note"><strong>Note:</strong> {fin(esc(mk(q["note"])))}</div>' if q.get("note") else ""
    task = f'<span class="qa-badge">Task {esc(q["task"])}</span>' if q.get("task") else ""
    multi = f'<span class="qa-badge multi">Choose {len(q["correct"])}</span>' if q["type"] == "multi" else ""
    badge = f'<span class="qa-badge pct">{round(pct * 100)}% match</span>' if pct is not None and pct < 1.0 else ""
    missing = f'<div class="qa-miss">Not found in this question: {esc(", ".join(miss))}</div>' if miss else ""
    return (
        f'<div class="qa-card" id="q{q["id"]}"><div class="qa-meta"><span class="qa-badge id">Q{q["id"]}</span>'
        f'<span class="qa-badge">{esc(q["category"])}</span>{task}{multi}{badge}</div>'
        f'<div class="qa-q">{paras}</div><ul class="qa-opts">{opts}</ul>'
        f'<div class="qa-ans">Answer: {", ".join(q["correct"])}</div>{missing}'
        f'<details class="qa-ex"{open_attr}><summary>Explanation</summary>'
        f'<div class="qa-exb">{md_lite(q.get("explanation", ""), mk)}</div></details>'
        f'{note}<div class="qa-foot">{votes}{ref}</div></div>'
    )


# --------------------------------------------------------------------------- #
# 🔎 Browse & search
# --------------------------------------------------------------------------- #
def browse_view():
    if "q" not in st.session_state:
        st.session_state.q = st.query_params.get("q", "")

    c1, c2, c3 = st.columns([5, 2, 2], vertical_alignment="center")
    query = c1.text_input(
        "Search", key="q", label_visibility="collapsed",
        placeholder='Search, or paste a question…   e.g.  guardrails  |  "least operational overhead"  |  #82',
    )
    qtype = c2.selectbox("Type", ["All types", "Choose one", "Choose two or more"], label_visibility="collapsed")
    expand = c3.toggle("Show explanations", value=False)
    domains = st.multiselect("Domains", DOMAINS, placeholder="All domains", label_visibility="collapsed")

    if query:
        st.query_params["q"] = query
    elif "q" in st.query_params:
        del st.query_params["q"]

    pool = [
        q for q in IDX.items
        if (not domains or q["category"] in domains)
        and (qtype == "All types" or (qtype == "Choose one") == (q["type"] == "single"))
    ]
    results, mode, info, words, phrases = IDX.search(pool, query)
    mk = make_marker(words, phrases)

    if mode == "all":
        st.caption(f"Showing **{len(results)}** of {IDX.n} questions · answers are shown on every card")
    else:
        used = " · ".join([f"`{t}`" for t, _ in words] + [f'"{p.strip()}"' for p in phrases]
                          + [f"`#{i}`" for i in sorted(IDX.parse(query)[2])])
        skipped = "whole words only · filler words like *a, the, to, is* are ignored · simple plurals match"
        if mode == "exact":
            st.caption(f"**{len(results)}** of {IDX.n} questions contain all of: {used}  ·  best match first  ·  {skipped}")
        elif results:
            st.warning(f"No question contains every word of: {used}. Showing the **{len(results)} closest** "
                       f"(best first, with % of your words found). Tip: quote a phrase or drop a word to narrow it.")
        else:
            st.info(f"Nothing matches {used}. Try fewer or different words.")

    if results:
        st.markdown("".join(card(q, mk, expand, words, phrases, *info.get(q["id"], (None, ()))) for q in results),
                    unsafe_allow_html=True)
    elif mode == "exact":
        st.info("No questions match. Try fewer or different words, or clear the filters.")


# --------------------------------------------------------------------------- #
# 📷 Scan a question
# --------------------------------------------------------------------------- #
@st.cache_data(show_spinner=False, max_entries=30)
def analyze_photo(jpeg: bytes, model: str, attempt: int, _key: str) -> dict:
    """Cached per image, so editing the text or re-running never re-bills the API.
    `attempt` is part of the cache key: 'Read again' bumps it to force a fresh read."""
    return scan.read_photo(jpeg, _key, model)


def parse_options_text(text: str) -> list[tuple[str, str]]:
    """'A. foo\\nB. bar' (as edited by the user) -> [('A','foo'), ('B','bar')]."""
    out, plain = [], []
    for line in text.splitlines():
        if not line.strip():
            continue
        m = re.match(r"^\s*([A-Fa-f])\s*[\.\)\:]\s*(.*)$", line)
        if m:
            out.append([m.group(1).upper(), m.group(2).strip()])
        elif out:
            out[-1][1] += " " + line.strip()
        else:
            plain.append(line.strip())
    if not out and plain:                         # no letters at all: one option per line
        out = [[chr(65 + i), t] for i, t in enumerate(plain)]
    return [(l, t) for l, t in out if t]


def show_match(matches: list[dict], options: list[tuple[str, str]]):
    best = matches[0]
    b, pct = band(best["score"]), round(best["score"] * 100)
    q = IDX.by_id[best["id"]]
    photo_text = dict(options)

    if b == "none":
        st.error(f"❌ **Not found in this bank.** The closest question is Q{q['id']} at only {pct}% — "
                 "probably a different question. (If the AI misread the photo, fix the text below.)")
        with st.expander(f"Closest question anyway: Q{q['id']} ({pct}%)"):
            st.markdown(card(q, pct=best["score"]), unsafe_allow_html=True)
        return

    (st.success if b == "sure" else st.warning)(
        f"{'✅ Found in the bank' if b == 'sure' else '🟡 Probably this one — compare the wording'}"
        f" — **Q{q['id']}** · {pct}% match")

    letters = ", ".join(q["correct"])
    mapped = [best["to_photo"].get(c) for c in q["correct"]]
    extra = ""
    if all(mapped):
        pl = ", ".join(sorted(mapped))
        snippet = (photo_text.get(mapped[0], "")[:70] + "…") if len(mapped) == 1 and photo_text.get(mapped[0]) else ""
        extra = (f"<small>In your photo that is option <b>{pl}</b>" + (f": “{esc(snippet)}”" if snippet else "") + "</small>")
    st.markdown(f'<div class="scan-ans">Answer: {letters}{extra}</div>', unsafe_allow_html=True)
    st.markdown(card(q, pct=best["score"]), unsafe_allow_html=True)

    others = [m for m in matches[1:3] if band(m["score"]) != "none"]
    if others:
        with st.expander("Other close matches"):
            for m in others:
                st.markdown(card(IDX.by_id[m["id"]], pct=m["score"]), unsafe_allow_html=True)


def scan_view():
    st.caption("Take a photo of a question from **any** practice set — the answer doesn't need to be shown. "
               "I'll read it and look it up in this bank.")

    key = secret("OPENAI_API_KEY") or st.session_state.get("manual_key")
    model = secret("OPENAI_MODEL", scan.DEFAULT_MODEL)
    pw = secret("APP_PASSWORD")

    if pw and not st.session_state.get("unlocked"):
        entered = st.text_input("Passcode to use the photo scanner", type="password", key="pw_in")
        if entered:
            if hmac.compare_digest(entered.encode(), str(pw).encode()):
                st.session_state.unlocked = True
                st.rerun()
            st.error("Wrong passcode.")
        return
    if not key:
        with st.expander("🔑 Photo scanning needs an OpenAI API key", expanded=True):
            st.markdown("Add `OPENAI_API_KEY` in the app's **Secrets** (Streamlit Cloud → ⋮ → Settings → Secrets), "
                        "or paste a key here for this session only (it isn't stored).")
            k = st.text_input("OpenAI API key", type="password", key="manual_key_in")
            if k:
                st.session_state.manual_key = k
                st.rerun()
        st.caption("Meanwhile you can still paste a question's text under **🔎 Browse & search**.")
        return

    # Two states: no photo yet -> show the camera/upload inputs; photo taken -> hide them so the
    # result sits at the top of the screen (important on a phone), with a button to scan another.
    photo = st.session_state.get("scan_img")
    if photo is None:
        t_up, t_cam = st.tabs(["📸 Take photo / upload", "🎥 Live camera"])
        with t_up:
            up = st.file_uploader("Take a photo or choose an image", key="scan_up", label_visibility="collapsed",
                                  type=["jpg", "jpeg", "png", "webp", "heic", "heif"])
            st.caption("On a phone this opens your camera app — best quality. Good light, fill the frame, hold steady.")
        with t_cam:
            cam = st.camera_input("Point the camera at the question", key="scan_cam", label_visibility="collapsed")
            st.caption("If the camera doesn't start, allow camera access for this site, or use *Take photo / upload*.")
        picked = cam or up
        if picked is not None:
            st.session_state.scan_img = picked.getvalue()
            st.rerun()
        st.info("**Tips:** one question per photo · include the whole question *and* all options · "
                "avoid glare on screens · a blurry photo is fine as long as the words are legible.")
        return

    def scan_another(where: str):
        if st.button("📷 Scan another question", use_container_width=True, type="primary", key=f"another_{where}"):
            st.session_state.pop("scan_img", None)
            st.rerun()

    scan_another("top")
    sig = hashlib.sha1(photo).hexdigest()[:10]

    with st.expander("Your photo", expanded=False):
        st.image(photo, use_container_width=True)

    attempt = st.session_state.get(f"try_{sig}", 0)

    def read_again(label: str, k: str):
        if st.button(label, key=k):
            st.session_state[f"try_{sig}"] = attempt + 1
            for name in [n for n in st.session_state if n.startswith((f"stem_{sig}", f"opts_{sig}"))]:
                del st.session_state[name]
            st.rerun()

    try:
        with st.spinner("Reading the photo…"):
            res = analyze_photo(scan.prepare_image(photo), model, attempt, key)
    except scan.ScanError as e:
        st.error(str(e))
        read_again("🔄 Try again", f"retry_err_{sig}")
        res = None

    if res is not None and not res["questions"]:
        st.warning("I couldn't read a question in that photo." + (f" ({res['problem']})" if res["problem"] else "")
                   + " Try again closer, flatter and with more light, or read this photo once more.")
        read_again("🔄 Read this photo again", f"retry_empty_{sig}")
    for i, q in enumerate(res["questions"] if res else []):
        if len(res["questions"]) > 1:
            st.markdown(f"##### Question {i + 1} in the photo")
        if q["readability"] != "clear":
            st.caption("⚠️ Parts of this photo were hard to read — check the text below.")
        sk, ok = f"stem_{sig}_{i}", f"opts_{sig}_{i}"
        st.session_state.setdefault(sk, q["question"])
        st.session_state.setdefault(ok, "\n".join(f"{l}. {t}" for l, t in q["options"]))
        options = parse_options_text(st.session_state[ok])
        matches = IDX.match_photo(st.session_state[sk], options)
        if matches and matches[0]["score"] > 0:
            show_match(matches, options)
        else:
            st.error("Nothing to match yet — the question text looks empty.")
        with st.expander("✏️ What I read from the photo — fix it if it's wrong"):
            st.text_area("Question", key=sk, height=150)
            st.text_area("Options (one per line: A. text)", key=ok, height=200)
            st.caption("Edit and the match updates immediately; this doesn't call the AI again.")
            read_again("🔄 Read the photo again (calls the AI again)", f"retry_{sig}_{i}")

    scan_another("bottom")


# --------------------------------------------------------------------------- #
# Page
# --------------------------------------------------------------------------- #
st.subheader("AIP-C01 · Question & Answer")
if "view" not in st.session_state:
    st.session_state.view = BROWSE_VIEW if st.query_params.get("q") else SCAN_VIEW
view = st.segmented_control("View", [SCAN_VIEW, BROWSE_VIEW], key="view", label_visibility="collapsed") or SCAN_VIEW
(scan_view if view == SCAN_VIEW else browse_view)()
