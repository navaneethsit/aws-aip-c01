"""
AIP-C01 Question & Answer Browser — every question with its choices and correct answer,
plus a search box. No mock exam, no timer, no scoring.

Search rules
  * whole words only (so "a" never matches inside "application"); simple plurals are tolerated
  * filler words (a, the, to, is, ...) are ignored, so you can paste a sentence from a question
  * every remaining word must appear; "quoted phrases" must appear exactly; #82 = question number
  * results are ranked by relevance (rarer words and hits in the question text count more)
  * if no question contains every word, the closest matches are shown with a % match

Run locally:   streamlit run app.py
Deploy:        Streamlit Community Cloud (see README.md)
"""
import html
import math
import re
from collections import Counter

import streamlit as st

from questions import QUESTIONS

st.set_page_config(page_title="AIP-C01 Q&A", page_icon="🔎", layout="wide")

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
    </style>
    """,
    unsafe_allow_html=True,
)

# --------------------------------------------------------------------------- #
# Text handling
# --------------------------------------------------------------------------- #
STOP = set(
    "a an the of to in on for and or is are was were be been being with by as at from that this these those "
    "it its into than then so such do does did have has had can could should would will may might must which "
    "who whom whose what when where why how their there they them we you your our us i if but about over under "
    "per via also any all each other some more most only both between within without after before up out off "
    "not no yes".split()
)


def tokens(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", s.lower())


def stem(w: str) -> str:
    """Tolerate simple plurals: guardrails -> guardrail, responses -> response."""
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w


# --------------------------------------------------------------------------- #
# Index
# --------------------------------------------------------------------------- #
@st.cache_data
def load_index():
    items, df = [], Counter()
    for q in sorted(QUESTIONS, key=lambda x: x["id"]):
        answers = [q["answer"]] if q["type"] == "single" else list(q["answer"])
        correct = {a[0] for a in answers}
        opts = [{"letter": o[0], "text": o[3:].strip(), "ok": o[0] in correct} for o in q["options"]]
        q_toks = tokens(q["question"])
        o_toks = tokens(" ".join(o["text"] for o in opts))
        x_toks = tokens(" ".join([q.get("explanation", ""), q.get("note", ""), q["category"],
                                  q.get("reference", ""), " ".join(sorted(correct))]))
        q_s, o_s, x_s = ({stem(t) for t in ts} for ts in (q_toks, o_toks, x_toks))
        for s in q_s | o_s | x_s:
            df[s] += 1
        items.append({
            **q, "opts": opts, "correct": sorted(correct),
            "q_s": q_s, "o_s": o_s, "x_s": x_s,
            "main_norm": " " + " ".join(q_toks + o_toks) + " ",
            "all_norm": " " + " ".join(q_toks + o_toks + x_toks) + " ",
        })
    return items, dict(df)


ITEMS, DF = load_index()
N = len(ITEMS)
DOMAINS = list(dict.fromkeys(q["category"] for q in ITEMS))
IDS = {q["id"] for q in ITEMS}


def idf(s: str) -> float:
    return math.log(1 + N / (1 + DF.get(s, 0)))


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #
def parse(query: str):
    phrases = [" " + " ".join(tokens(p)) + " " for p in re.findall(r'"([^"]+)"', query) if tokens(p)]
    rest = re.sub(r'"[^"]*"', " ", query)
    ids = {int(n) for n in re.findall(r"#(\d+)", rest)}
    rest = re.sub(r"#\d+", " ", rest)
    words, seen = [], set()                      # (what the user typed, stem)
    for t in tokens(rest):
        if t in STOP or (len(t) == 1 and not t.isdigit()):
            continue
        s = stem(t)
        if s not in seen:
            seen.add(s)
            words.append((t, s))
    return words, phrases, ids


def has_word(q: dict, s: str) -> int:
    """0 = absent, else a boost: 3 question text, 2 options, 1 explanation/other."""
    if s in q["q_s"]:
        return 3
    if s in q["o_s"]:
        return 2
    if s in q["x_s"]:
        return 1
    m = re.fullmatch(r"q?(\d+)", s)               # "82" / "q82" also hit question number 82
    return 3 if m and int(m.group(1)) == q["id"] else 0


def search(items, query: str):
    words, phrases, ids = parse(query)
    if not (words or phrases or ids):
        return items, "all", {}, words, phrases
    total_w = sum(idf(s) for _, s in words) or 1.0
    scored = []
    for q in items:
        if ids and q["id"] not in ids:
            continue
        if any(p not in q["all_norm"] for p in phrases):
            continue
        hit = {s: has_word(q, s) for _, s in words}
        got = sum(idf(s) for s, b in hit.items() if b)
        score = sum(idf(s) * b for s, b in hit.items() if b) + 4 * len(phrases)
        scored.append((q, hit, got / total_w, score))
    strict = [x for x in scored if all(x[1].values())]
    if strict or len(words) < 2:
        strict.sort(key=lambda x: (-x[3], x[0]["id"]))
        return [x[0] for x in strict], "exact", {x[0]["id"]: (1.0, []) for x in strict}, words, phrases
    near = [x for x in scored if x[2] >= 0.5 and sum(1 for b in x[1].values() if b) >= 2]
    near.sort(key=lambda x: (-x[2], -x[3], x[0]["id"]))
    near = near[:15]
    info = {x[0]["id"]: (x[2], [t for t, s in words if not x[1][s]]) for x in near}
    return [x[0] for x in near], "closest", info, words, phrases


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


def card(q: dict, mk, expand: bool, words, phrases, extra: tuple | None) -> str:
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
    pct = miss = ""
    if extra and extra[0] < 1.0:
        pct = f'<span class="qa-badge pct">{round(extra[0] * 100)}% match</span>'
        miss = (f'<div class="qa-miss">Not found in this question: {esc(", ".join(extra[1]))}</div>' if extra[1] else "")
    return (
        f'<div class="qa-card" id="q{q["id"]}"><div class="qa-meta"><span class="qa-badge id">Q{q["id"]}</span>'
        f'<span class="qa-badge">{esc(q["category"])}</span>{task}{multi}{pct}</div>'
        f'<div class="qa-q">{paras}</div><ul class="qa-opts">{opts}</ul>'
        f'<div class="qa-ans">Answer: {", ".join(q["correct"])}</div>{miss}'
        f'<details class="qa-ex"{open_attr}><summary>Explanation</summary>'
        f'<div class="qa-exb">{md_lite(q.get("explanation", ""), mk)}</div></details>'
        f'{note}<div class="qa-foot">{votes}{ref}</div></div>'
    )


# --------------------------------------------------------------------------- #
# UI
# --------------------------------------------------------------------------- #
if "q" not in st.session_state:
    st.session_state.q = st.query_params.get("q", "")

st.subheader("AIP-C01 · Question & Answer Browser")

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
    q for q in ITEMS
    if (not domains or q["category"] in domains)
    and (qtype == "All types" or (qtype == "Choose one") == (q["type"] == "single"))
]
results, mode, info, words, phrases = search(pool, query)
mk = make_marker(words, phrases)

if mode == "all":
    st.caption(f"Showing **{len(results)}** of {N} questions · answers are shown on every card")
else:
    used = " · ".join([f"`{t}`" for t, _ in words] + [f'"{p.strip()}"' for p in phrases]
                      + [f"`#{i}`" for i in sorted(parse(query)[2])])
    skipped = "whole words only · filler words like *a, the, to, is* are ignored · simple plurals match"
    if mode == "exact":
        st.caption(f"**{len(results)}** of {N} questions contain all of: {used}  ·  best match first  ·  {skipped}")
    elif results:
        st.warning(f"No question contains every word of: {used}. Showing the **{len(results)} closest** "
                   f"(best first, with % of your words found). Tip: quote a phrase or drop a word to narrow it.")
    else:
        st.info(f"Nothing matches {used}. Try fewer or different words.")

if results:
    st.markdown("".join(card(q, mk, expand, words, phrases, info.get(q["id"])) for q in results), unsafe_allow_html=True)
elif mode == "exact":
    st.info("No questions match. Try fewer or different words, or clear the filters.")
