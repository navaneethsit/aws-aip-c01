"""
Search + photo-matching engine (no Streamlit imports, so it can be tested on its own).

Text search
  * whole words only, simple plurals tolerated, filler words ignored
  * every remaining word must appear; "quoted phrases" must appear; #82 = question number
  * ranked by relevance; falls back to the closest matches when nothing contains every word

Photo matching (`match_photo`)
  Takes what a vision model read from a photo (question stem + options) and ranks the bank by how
  well it matches. It is built to survive OCR damage and other dumps' wording:
  * likely OCR typos are repaired once up front: a word that exists nowhere in the bank is mapped to
    its closest bank word ("applicaton" -> "application")
  * idf-weighted word overlap (rare words matter more than "company")
  * the stem and the options are scored separately and combined, so a cropped stem or a missing
    option still finds the question
  * options are matched by *text*, not letter, so shuffled options still map to the right answer
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


def tokens(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", s.lower())


def stem(w: str) -> str:
    """Tolerate simple plurals: guardrails -> guardrail, responses -> response."""
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w


def content_stems(text: str) -> list[str]:
    return [stem(t) for t in tokens(text) if t not in STOP and (len(t) > 1 or t.isdigit())]


class Index:
    def __init__(self, questions: list[dict]):
        self.items: list[dict] = []
        df: Counter = Counter()
        for q in sorted(questions, key=lambda x: x["id"]):
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
            self.items.append({
                **q, "opts": opts, "correct": sorted(correct),
                "q_s": q_s, "o_s": o_s, "x_s": x_s,
                "main_norm": " " + " ".join(q_toks + o_toks) + " ",
                "all_norm": " " + " ".join(q_toks + o_toks + x_toks) + " ",
                # for photo matching: content-word sets
                "q_c": set(content_stems(q["question"])),
                "opt_c": [(o["letter"], set(content_stems(o["text"]))) for o in opts],
            })
        self.df = dict(df)
        self.n = len(self.items)
        self.by_id = {q["id"]: q for q in self.items}
        self._idf = {s: math.log(1 + self.n / (1 + c)) for s, c in self.df.items()}
        self._idf_unseen = math.log(1 + self.n)          # a word that appears nowhere in the bank
        self.vocab = sorted(self.df)
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

    def _words(self, text: str) -> set[str]:
        return {self._fix(s) for s in content_stems(text)}

    def _cov(self, a: set, b: set) -> float:
        """idf-weighted share of the words in `a` that also appear in `b`."""
        if not a:
            return 0.0
        tot = sum(self.idf(s) for s in a)
        return sum(self.idf(s) for s in a if s in b) / tot if tot else 0.0

    def _sim(self, p: set, b: set) -> float:
        return 0.65 * self._cov(p, b) + 0.35 * self._cov(b, p)

    def _score(self, q: dict, P: set, photo_opts: list[tuple[str, set]]):
        s_score = self._sim(P, q["q_c"]) if len(P) >= 4 else None
        o_score, mapping = None, {}
        if len(photo_opts) >= 2:
            pairs = []
            for i, (_, pc) in enumerate(photo_opts):
                for j, (_, bc) in enumerate(q["opt_c"]):
                    pairs.append((self._sim(pc, bc) if pc and bc else 0.0, i, j))
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
        P = self._words(stem_text)
        photo_opts = [(l, self._words(t)) for l, t in options if t.strip()]
        out = []
        for q in self.items:
            sc, ss, os_, mp = self._score(q, P, photo_opts)
            to_photo = {b: p for p, (b, sim) in mp.items() if sim >= 0.45}
            out.append({"id": q["id"], "score": sc, "stem": ss, "opts": os_, "to_photo": to_photo})
        out.sort(key=lambda r: -r["score"])
        return out[:top]


# Confidence bands for a photo match (tuned against noisy simulations)
SURE, PROBABLE = 0.72, 0.52


def band(score: float) -> str:
    return "sure" if score >= SURE else "probable" if score >= PROBABLE else "none"
