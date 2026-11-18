"""
Search + photo-matching engine (no Streamlit imports, so it can be tested on its own).

Text search
  * whole words only, simple plurals tolerated, filler words ignored
  * every remaining word must appear; "quoted phrases" must appear; #82 = question number
  * ranked by relevance; falls back to the closest matches when nothing contains every word

Photo matching (`match_photo`) takes what a vision model read from a photo (question stem + options)
and ranks the bank. It combines two independent signals and keeps the better one:
  * WORDS: idf-weighted overlap (rare words matter more than "company"). Likely OCR typos are repaired
    first (a word found nowhere in the bank -> its closest bank word) and words split across lines
    ("Bed-rock", "docu ments") are re-joined.
  * CHARACTERS: idf-weighted overlap of 6-character shingles of the text with spaces/punctuation
    removed. This survives hyphenation, odd spacing, OCR slips and light rewording.
The stem and the options are scored separately and combined, so a cropped stem or a missing option
still finds the question. Options are matched by *text*, not letter, so shuffled options map to the
right answer. `decide()` turns the ranked list into a confidence band using the score AND the margin
over the runner-up (a low score with a clear winner is still a likely match).
"""
from __future__ import annotations

import difflib
import math
import re
from collections import Counter

STOP = set(
    "a an the of to in on for and or is are was were be been being with by as at from that this these those "
    "it its into than then so such do does did have has had can could should would will may might must which "
    "who whom whose what when where why how their there they them we you your our us i if but about over under "
    "per via also any all each other some more most only both between within without after before up out off "
    "not no yes".split()
)
GRAM = 6


def tokens(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", s.lower())


def stem(w: str) -> str:
    """Tolerate simple plurals: guardrails -> guardrail, responses -> response."""
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w


def content_stems(text: str) -> list[str]:
    return [stem(t) for t in tokens(text) if t not in STOP and (len(t) > 1 or t.isdigit())]


def grams(text: str, n: int = GRAM) -> set[str]:
    t = re.sub(r"[^a-z0-9]", "", text.lower())
    return {t[i:i + n] for i in range(len(t) - n + 1)} if len(t) >= n else ({t} if t else set())


class Index:
    def __init__(self, questions: list[dict]):
        self.items: list[dict] = []
        df: Counter = Counter()
        gdf: Counter = Counter()
        raw_vocab: set[str] = set()
        for q in sorted(questions, key=lambda x: x["id"]):
            answers = [q["answer"]] if q["type"] == "single" else list(q["answer"])
            correct = {a[0] for a in answers}
            opts = [{"letter": o[0], "text": o[3:].strip(), "ok": o[0] in correct} for o in q["options"]]
            q_toks = tokens(q["question"])
            o_toks = tokens(" ".join(o["text"] for o in opts))
            x_toks = tokens(" ".join([q.get("explanation", ""), q.get("note", ""), q["category"],
                                      q.get("reference", ""), " ".join(sorted(correct))]))
            raw_vocab.update(q_toks, o_toks, x_toks)
            q_s, o_s, x_s = ({stem(t) for t in ts} for ts in (q_toks, o_toks, x_toks))
            for s in q_s | o_s | x_s:
                df[s] += 1
            g_q, g_o = grams(q["question"]), [grams(o["text"]) for o in opts]
            for g in g_q.union(*g_o):
                gdf[g] += 1
            self.items.append({
                **q, "opts": opts, "correct": sorted(correct),
                "q_s": q_s, "o_s": o_s, "x_s": x_s,
                "main_norm": " " + " ".join(q_toks + o_toks) + " ",
                "all_norm": " " + " ".join(q_toks + o_toks + x_toks) + " ",
                # for photo matching
                "q_c": set(content_stems(q["question"])),
                "opt_c": [(o["letter"], set(content_stems(o["text"]))) for o in opts],
                "g_q": g_q, "g_o": g_o,
            })
        self.df = dict(df)
        self.n = len(self.items)
        self.by_id = {q["id"]: q for q in self.items}
        self._idf = {s: math.log(1 + self.n / (1 + c)) for s, c in self.df.items()}
        self._idf_unseen = math.log(1 + self.n)          # a word that appears nowhere in the bank
        self._gidf = {g: math.log(1 + self.n / (1 + c)) for g, c in gdf.items()}
        self.vocab = sorted(self.df)
        self.raw_vocab = raw_vocab
        self._fix_cache: dict[str, str] = {}

    # ------------------------------------------------------------------ #
    def idf(self, s: str) -> float:
        return self._idf.get(s, self._idf_unseen)

    # ------------------------------------------------------------------ text search
    @staticmethod
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

    @staticmethod
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

    def search(self, items: list[dict], query: str):
        """-> (results, mode, info, words, phrases); mode in {'all','exact','closest'}."""
        words, phrases, ids = self.parse(query)
        if not (words or phrases or ids):
            return items, "all", {}, words, phrases
        total_w = sum(self.idf(s) for _, s in words) or 1.0
        scored = []
        for q in items:
            if ids and q["id"] not in ids:
                continue
            if any(p not in q["all_norm"] for p in phrases):
                continue
            hit = {s: self.has_word(q, s) for _, s in words}
            got = sum(self.idf(s) for s, b in hit.items() if b)
            score = sum(self.idf(s) * b for s, b in hit.items() if b) + 4 * len(phrases)
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

    # ------------------------------------------------------------------ photo matching
    def _fix(self, s: str) -> str:
        """Repair a likely OCR typo: a word found nowhere in the bank -> its closest bank word."""
        if s in self.df or len(s) < 5 or s.isdigit():
            return s
        hit = self._fix_cache.get(s)
        if hit is None:
            cands = [v for v in self.vocab
                     if abs(len(v) - len(s)) <= 2 and (v[0] == s[0] or v[-1] == s[-1])]
            m = difflib.get_close_matches(s, cands, n=1, cutoff=0.82)
            hit = self._fix_cache[s] = m[0] if m else s
        return hit

    def _rejoin(self, toks: list[str]) -> list[str]:
        """Re-join words split by line wraps/hyphenation: ['bed','rock'] -> ['bedrock'] when that is a
        real bank word and at least one fragment is not."""
        out, i = [], 0
        while i < len(toks):
            if i + 1 < len(toks):
                a, b = toks[i], toks[i + 1]
                if len(a) >= 2 and len(b) >= 2 and a + b in self.raw_vocab and (a not in self.raw_vocab or b not in self.raw_vocab):
                    out.append(a + b)
                    i += 2
                    continue
            out.append(toks[i])
            i += 1
        return out

    def _words(self, text: str) -> set[str]:
        toks = self._rejoin(tokens(text))
        return {self._fix(stem(t)) for t in toks if t not in STOP and (len(t) > 1 or t.isdigit())}

    def _cov(self, a: set, b: set) -> float:
        """idf-weighted share of the words in `a` that also appear in `b`."""
        if not a:
            return 0.0
        tot = sum(self.idf(s) for s in a)
        return sum(self.idf(s) for s in a if s in b) / tot if tot else 0.0

    def _gcov(self, a: set, b: set) -> float:
        if not a:
            return 0.0
        unseen = self._idf_unseen
        tot = hit = 0.0
        for g in a:
            w = self._gidf.get(g, unseen)
            tot += w
            if g in b:
                hit += w
        return hit / tot if tot else 0.0

    def _sim(self, pw: set, bw: set, pg: set, bg: set) -> float:
        """Best of the word signal and the character signal."""
        words = 0.65 * self._cov(pw, bw) + 0.35 * self._cov(bw, pw)
        chars = 0.65 * self._gcov(pg, bg) + 0.35 * self._gcov(bg, pg)
        return max(words, chars)

    def _score(self, q: dict, P: set, Pg: set, photo_opts: list[tuple[str, set, set]]):
        s_score = self._sim(P, q["q_c"], Pg, q["g_q"]) if (len(P) >= 4 or len(Pg) >= 20) else None
        o_score, mapping = None, {}
        if len(photo_opts) >= 2:
            pairs = []
            for i, (_, pw, pg) in enumerate(photo_opts):
                for j, (_, bw) in enumerate(q["opt_c"]):
                    pairs.append((self._sim(pw, bw, pg, q["g_o"][j]) if (pw or pg) and bw else 0.0, i, j))
            pairs.sort(reverse=True)
            used_i, used_j, got = set(), set(), {}
            for sim, i, j in pairs:
                if i in used_i or j in used_j:
                    continue
                used_i.add(i); used_j.add(j); got[i] = (j, sim)
            o_score = sum(got.get(i, (0, 0.0))[1] for i in range(len(photo_opts))) / len(photo_opts)
            for i, (j, sim) in got.items():
                mapping[photo_opts[i][0]] = (q["opt_c"][j][0], sim)   # photo letter -> bank letter
        if s_score is None and o_score is None:
            return 0.0, None, None, {}
        if s_score is None or o_score is None:
            final = s_score if o_score is None else o_score
        else:
            final = 0.6 * max(s_score, o_score) + 0.4 * min(s_score, o_score)
        return final, s_score, o_score, mapping

    def match_photo(self, stem_text: str, options: list[tuple[str, str]], top: int = 5) -> list[dict]:
        """Rank the bank against a question read from a photo.

        options: [(letter_in_photo, option_text), ...]  ->  list of dicts sorted best-first:
          {id, score, stem, opts, to_photo}  where to_photo maps bank letter -> photo letter.
        """
        P, Pg = self._words(stem_text), grams(stem_text)
        photo_opts = [(l, self._words(t), grams(t)) for l, t in options if t.strip()]
        out = []
        for q in self.items:
            sc, ss, os_, mp = self._score(q, P, Pg, photo_opts)
            to_photo = {b: p for p, (b, sim) in mp.items() if sim >= 0.45}
            out.append({"id": q["id"], "score": sc, "stem": ss, "opts": os_, "to_photo": to_photo})
        out.sort(key=lambda r: -r["score"])
        return out[:top]


# --------------------------------------------------------------------------- #
# Confidence
# --------------------------------------------------------------------------- #
SURE, PROBABLE = 0.72, 0.52          # absolute score bands
POSSIBLE_MIN, CLEAR_RATIO = 0.22, 1.7  # a low score still counts if it clearly beats the runner-up


def margin(matches: list[dict]) -> float:
    """How many times better the best match is than the runner-up."""
    if not matches:
        return 0.0
    s1 = matches[0]["score"]
    s2 = matches[1]["score"] if len(matches) > 1 else 0.0
    return s1 / max(s2, 0.05)


def decide(matches: list[dict]) -> str:
    """'sure' | 'probable' | 'possible' | 'none' using the score and the margin over the runner-up."""
    if not matches:
        return "none"
    s, r = matches[0]["score"], margin(matches)
    if s >= SURE or (s >= 0.55 and r >= 2.5):
        return "sure"
    if s >= PROBABLE or (s >= 0.35 and r >= CLEAR_RATIO):
        return "probable"
    if s >= POSSIBLE_MIN and r >= CLEAR_RATIO:
        return "possible"
    return "none"


def band(score: float) -> str:
    """Score-only band (kept for callers that have no runner-up)."""
    return "sure" if score >= SURE else "probable" if score >= PROBABLE else "none"
