"""Pure-Python matching core (no QGIS imports, so it can be unit-tested with plain Python).

Pipeline per table row
  1. exact        - same text after trim/case
  2. normalized   - same text after accents, punctuation and noise words are removed
  3. phonetic     - same "transliteration key" (th->t, dh->d, kk->k, oo->u, ee->i, g->k ...)
  4. fuzzy        - best similarity score over a shortlist (trigram index), accepted if high and unambiguous
  5. ai           - optional: an LLM chooses among the REAL shortlisted candidates (it can never invent an id),
                    and can suggest other names (old name, official name) that are then searched in the layer

Every decision is returned with a method, a score and a reason so the user can review it.
"""
import re
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher

DEFAULT_NOISE = (
    "district dt dist town city village panchayat panchayath panchayat union special grade selection "
    "municipality municipal corporation limits cantonment board rural urban taluk tk block ward the of"
)

_PHONETIC_RULES = (
    ("zh", "l"), ("th", "t"), ("dh", "d"), ("sh", "s"), ("ch", "c"), ("kh", "k"), ("gh", "g"),
    ("ph", "p"), ("bh", "b"), ("jh", "j"), ("w", "v"), ("ee", "i"), ("oo", "u"), ("aa", "a"),
    ("ou", "u"), ("ae", "e"), ("ay", "ai"), ("ey", "ei"), ("q", "k"), ("x", "ks"), ("z", "s"),
    # Tamil script has one letter for k/g, t/d, p/b, c/s: English spellings use either
    ("g", "k"), ("d", "t"), ("b", "p"), ("c", "s"), ("y", "i"), ("o", "u"), ("e", "i"),
)


def strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def normalize(s, noise=frozenset(DEFAULT_NOISE.split())):
    if s is None:
        return ""
    s = strip_accents(str(s)).lower()
    s = re.sub(r"\(.*?\)", " ", s)            # "Tiruvallur (M)" -> "Tiruvallur"
    s = re.sub(r"[^a-z0-9]+", " ", s)
    words = [w for w in s.split() if w not in noise]
    return " ".join(words)


def phonetic(s):
    """Spelling-insensitive key for romanised Indian place names. Input should be normalize()d."""
    k = s.replace(" ", "")
    for a, b in _PHONETIC_RULES:
        k = k.replace(a, b)
    k = re.sub(r"(.)\1+", r"\1", k)          # kk -> k, tt -> t, ii -> i
    k = re.sub(r"h", "", k)                   # leftover aspirations
    return k


def similarity(a_norm, a_key, b_norm, b_key):
    """0..1. Max of plain-text and phonetic-key similarity; spaces ignored on the plain side too."""
    if not a_norm or not b_norm:
        return 0.0
    s1 = SequenceMatcher(None, a_norm.replace(" ", ""), b_norm.replace(" ", "")).ratio()
    s2 = SequenceMatcher(None, a_key, b_key).ratio() if a_key and b_key else 0.0
    s3 = 0.0
    short, long_ = sorted((a_key, b_key), key=len)
    if len(short) >= 4 and short in long_ and long_ != short:      # "Velur" inside "Paramathi Velur"
        s3 = 0.80 + 0.1 * len(short) / len(long_)
    return max(s1, s2, s3)


def _grams(key):
    k = f"^{key}$"
    return {k[i:i + 3] for i in range(len(k) - 2)} or {k}


class Index:
    """Targets: list of dicts with keys id, name, group (optional), extra (optional str for the AI)."""

    def __init__(self, targets, noise):
        self.noise = noise
        self.t = []
        self.by_norm = defaultdict(list)
        self.by_key = defaultdict(list)
        self.by_gram = defaultdict(set)
        self.by_group = defaultdict(set)
        for i, t in enumerate(targets):
            n = normalize(t["name"], noise)
            k = phonetic(n)
            self.t.append({**t, "norm": n, "key": k, "i": i})
            self.by_norm[n].append(i)
            self.by_key[k].append(i)
            for g in _grams(k):
                self.by_gram[g].add(i)
            self.by_group[t.get("group")].add(i)

    def shortlist(self, name, group=None, k=8, pool=200):
        """Top-k candidates by similarity. If group is given, only that group is searched."""
        n = normalize(name, self.noise)
        key = phonetic(n)
        allowed = self.by_group.get(group) if group is not None else None
        counts = defaultdict(int)
        for g in sorted(_grams(key)):                      # sorted: reproducible across runs
            for i in self.by_gram.get(g, ()):
                if allowed is None or i in allowed:
                    counts[i] += 1
        rough = sorted(counts, key=lambda i: (-counts[i], i))[:pool]
        if allowed is not None and len(allowed) <= pool:
            rough = sorted(allowed)
        scored = [(similarity(n, key, self.t[i]["norm"], self.t[i]["key"]), i) for i in rough]
        scored.sort(key=lambda x: (-x[0], self.t[x[1]]["norm"], x[1]))
        return n, key, scored[:k]


def decide(row_name, row_group, index, auto_accept=0.92, min_margin=0.04, k=8):
    """Rule-based decision for one row. Returns dict; status 'matched' or 'review' or 'none'.

    With a group (e.g. district) the search is limited to that group. Tables often carry an old or wrong
    district, so when the in-group answer is only fuzzy, the whole layer is searched as well: a clean
    name hit in another group wins, but is sent to review ("other_group") instead of being accepted."""
    d = _decide(row_name, row_group, index, auto_accept, min_margin, k)
    if row_group is None or d["method"] in ("exact", "normalized", "phonetic", "ambiguous", "empty"):
        return d
    g = _decide(row_name, None, index, auto_accept, min_margin, k)
    if g["method"] in ("exact", "normalized", "phonetic") and index.t[g["i"]].get("group") != row_group:
        merged = g["cands"][:3] + [c for c in d["cands"] if c[1] not in {x[1] for x in g["cands"][:3]}]
        return _out("review", "other_group", g["score"], g["i"], merged[:k],
                    f"same name found in group '{index.t[g['i']].get('group')}', table says '{row_group}'")
    extra = [c for c in g["cands"][:2] if c[1] not in {x[1] for x in d["cands"]}]
    d["cands"] = d["cands"][:k - len(extra)] + extra          # let the reviewer / AI see out-of-group options
    return d


def _decide(row_name, row_group, index, auto_accept, min_margin, k):
    if row_name is None or str(row_name).strip() == "":
        return {"status": "none", "method": "empty", "score": 0.0, "cands": [], "reason": "empty name"}
    n, key, cands = index.shortlist(row_name, row_group, k=k)
    raw = str(row_name).strip().lower()
    exact = [c for c in cands if str(index.t[c[1]]["name"]).strip().lower() == raw]
    if len(exact) == 1:
        return _out("matched", "exact", 1.0, exact[0][1], cands, "same text")
    same_norm = [c for c in cands if index.t[c[1]]["norm"] == n]
    if len(same_norm) == 1:
        return _out("matched", "normalized", 1.0, same_norm[0][1], cands, "same after removing accents/punctuation/noise words")
    same_key = [c for c in cands if index.t[c[1]]["key"] == key]
    if len(same_key) == 1 and not same_norm:
        return _out("matched", "phonetic", 0.97, same_key[0][1], cands, "same transliteration key")
    if len(exact) > 1 or len(same_norm) > 1 or len(same_key) > 1:
        dup = exact or same_norm or same_key
        return _out("review", "ambiguous", dup[0][0], dup[0][1], cands, f"{len(dup)} places share this name")
    if not cands:
        return {"status": "none", "method": "none", "score": 0.0, "cands": [], "reason": "no candidate"}
    best, second = cands[0][0], (cands[1][0] if len(cands) > 1 else 0.0)
    if best >= auto_accept and best - second >= min_margin:
        return _out("matched", "fuzzy", best, cands[0][1], cands, f"similarity {best:.2f}, next {second:.2f}")
    return _out("review", "fuzzy_low", best, cands[0][1], cands, f"best {best:.2f}, next {second:.2f}")


def _out(status, method, score, i, cands, reason):
    return {"status": status, "method": method, "score": round(float(score), 3), "i": i, "cands": cands, "reason": reason}


def match_groups(table_groups, target_groups, noise, auto_accept=0.85):
    """Map each distinct table group value (e.g. district) to a target group value. Returns {table: target or None}."""
    idx = Index([{"id": g, "name": g, "group": None} for g in target_groups if g is not None], noise)
    out, info = {}, {}
    for g in table_groups:
        d = decide(g, None, idx, auto_accept=auto_accept, min_margin=0.03)
        hit = idx.t[d["i"]]["name"] if d["status"] == "matched" else None
        out[g] = hit
        info[g] = d
    return out, info, idx
