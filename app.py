"""
AIP-C01 Question & Answer Browser — every question with its choices and correct answer,
plus a search box. No mock exam, no timer, no scoring.

Run locally:   streamlit run app.py
Deploy:        Streamlit Community Cloud (see README.md)
"""
import html
import re

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
      .qa-foot{margin-top:10px;display:flex;gap:14px;flex-wrap:wrap;font-size:.8rem;opacity:.75;}
      .qa-foot a{color:inherit;}
      mark{background:rgba(255,213,0,.55);color:inherit;border-radius:3px;padding:0 1px;}
    </style>
    """,
    unsafe_allow_html=True,
)


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #
@st.cache_data
def load_items() -> list[dict]:
    items = []
    for q in sorted(QUESTIONS, key=lambda x: x["id"]):
        answers = [q["answer"]] if q["type"] == "single" else list(q["answer"])
        correct = {a[0] for a in answers}
        opts = [{"letter": o[0], "text": o[3:].strip(), "ok": o[0] in correct} for o in q["options"]]
        hay = "\n".join([
            f"q{q['id']}", q["category"], q["question"],
            " ".join(f"{o['letter']} {o['text']}" for o in opts),
            "answer " + " ".join(sorted(correct)),
            q.get("explanation", ""), q.get("note", ""), q.get("reference", ""),
        ]).lower()
        items.append({**q, "opts": opts, "correct": sorted(correct), "hay": hay})
    return items


ITEMS = load_items()
DOMAINS = list(dict.fromkeys(q["category"] for q in ITEMS))

# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #
def parse_terms(s: str) -> list[str]:
    return [(m.group(1) or m.group(2)).lower() for m in re.finditer(r'"([^"]+)"|(\S+)', s)]


def matches(q: dict, terms: list[str]) -> bool:
    for t in terms:
        if re.fullmatch(r"#\d+", t):               # #82 -> exactly question 82
            if q["id"] != int(t[1:]):
                return False
        elif re.fullmatch(r"q?\d+", t) and q["id"] == int(t.lstrip("q")):
            continue                                # "82" / "q82" also hit the question number
        elif t not in q["hay"]:
            return False
    return True


# --------------------------------------------------------------------------- #
# Rendering (each card is one line of HTML: no blank lines, so markdown leaves it alone)
# --------------------------------------------------------------------------- #
def esc(s: str) -> str:
    return html.escape(s).replace("$", "&#36;")      # $ would otherwise start LaTeX in st.markdown


def fin(s: str) -> str:
    return s.replace("\x02", "<mark>").replace("\x03", "</mark>")


def marker(rx):
    return (lambda text: rx.sub(lambda m: "\x02" + m.group(0) + "\x03", text)) if rx else (lambda text: text)


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


def card(q: dict, mk, expand: bool, rx) -> str:
    paras = "".join(f"<p>{fin(esc(mk(p)))}</p>" for p in q["question"].split("\n") if p.strip())
    opts = "".join(
        f'<li class="qa-opt{" ok" if o["ok"] else ""}"><span class="l">{o["letter"]}.</span>'
        f'<span>{fin(esc(mk(o["text"])))}</span>{"<span class=tick>✓ correct</span>" if o["ok"] else ""}</li>'
        for o in q["opts"]
    )
    open_attr = " open" if (expand or (rx and rx.search(q.get("explanation", "")))) else ""
    votes = ""
    if q.get("votes"):
        votes = "<span>Community vote: " + " · ".join(f"{esc(str(k))} {v}%" for k, v in q["votes"].items()) + "</span>"
    ref = (f'<a href="{html.escape(q["reference"])}" target="_blank" rel="noopener noreferrer">AWS / source doc ↗</a>'
           if q.get("reference") else "")
    note = f'<div class="qa-note"><strong>Note:</strong> {fin(esc(mk(q["note"])))}</div>' if q.get("note") else ""
    task = f'<span class="qa-badge">Task {esc(q["task"])}</span>' if q.get("task") else ""
    multi = f'<span class="qa-badge multi">Choose {len(q["correct"])}</span>' if q["type"] == "multi" else ""
    return (
        f'<div class="qa-card" id="q{q["id"]}"><div class="qa-meta"><span class="qa-badge id">Q{q["id"]}</span>'
        f'<span class="qa-badge">{esc(q["category"])}</span>{task}{multi}</div>'
        f'<div class="qa-q">{paras}</div><ul class="qa-opts">{opts}</ul>'
        f'<div class="qa-ans">Answer: {", ".join(q["correct"])}</div>'
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
    placeholder='Search questions, options, answers, explanations…   e.g.  guardrails  |  "least operational overhead"  |  #82',
)
qtype = c2.selectbox("Type", ["All types", "Choose one", "Choose two or more"], label_visibility="collapsed")
expand = c3.toggle("Show explanations", value=False)
domains = st.multiselect("Domains", DOMAINS, placeholder="All domains", label_visibility="collapsed")

if query:
    st.query_params["q"] = query
elif "q" in st.query_params:
    del st.query_params["q"]

terms = parse_terms(query)
words = [t for t in terms if not re.fullmatch(r"#?\d+|q\d+", t)]
rx = re.compile("(" + "|".join(re.escape(w) for w in words) + ")", re.I) if words else None
mk = marker(rx)

results = [
    q for q in ITEMS
    if (not domains or q["category"] in domains)
    and (qtype == "All types" or (qtype == "Choose one") == (q["type"] == "single"))
    and matches(q, terms)
]

st.caption(f"Showing **{len(results)}** of {len(ITEMS)} questions · answers are shown on every card · "
           'words match all terms · "quotes" match a phrase · `#82` jumps to a question number')

if results:
    st.markdown("".join(card(q, mk, expand, rx) for q in results), unsafe_allow_html=True)
else:
    st.info("No questions match. Try fewer or different words, or clear the filters.")
