"""Runs the whole match: groups -> rules -> (optional) AI. No QGIS imports.

rows:    [{"key": any, "name": str, "group": str|None, "info": str}]      the table
targets: [{"id": any, "name": str, "group": str|None, "extra": str}]      the layer
llm:     CachedLLM or None
Returns one result dict per row (same order) + a summary dict.
"""
from . import matcher as M

CHOOSE_SYSTEM = """You reconcile place names between two datasets.
Each item is one row of a table (name, group such as a district, extra info) and a numbered list of REAL
candidate features from a map layer. Pick the candidate that is the SAME real-world place.
Spelling variants, transliterations, old/new names, official vs common names and abbreviations all count
as the same place. A different place with a similar name does NOT count.
Use the group and info to break ties (for example a town with a large population is not a small hamlet).
The table's group can be outdated or wrong; a candidate from another group is allowed if you are sure.
If no candidate is the same place, answer choice 0. Never guess to fill a gap.
Answer only JSON: {"results":[{"row":<row>,"choice":<number or 0>,"confidence":<0..1>,"reason":"<max 12 words>"}]}"""

ALIAS_SYSTEM = """For each place give up to 3 OTHER names the same place is known by: official name, old
name, local or common name, or a very different spelling. Do not repeat the given name with tiny spelling
changes. Only list names you are confident about; an empty list is fine.
Answer only JSON: {"results":[{"row":<row>,"aliases":["...", "..."]}]}"""

CHOOSE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["results"], "properties": {
    "results": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                           "required": ["row", "choice", "confidence", "reason"],
                                           "properties": {"row": {"type": "integer"}, "choice": {"type": "integer"},
                                                          "confidence": {"type": "number"}, "reason": {"type": "string"}}}}}}
ALIAS_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["results"], "properties": {
    "results": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["row", "aliases"],
                                           "properties": {"row": {"type": "integer"},
                                                          "aliases": {"type": "array", "items": {"type": "string"}}}}}}}


def _items_json(items):
    import json
    return json.dumps(items, ensure_ascii=False, indent=0)


def run(rows, targets, llm=None, context="", noise=None, auto_accept=0.92, ai_min_conf=0.6,
        batch=20, use_groups=True, use_aliases=True, log=print, feedback=None):
    noise = frozenset((noise or M.DEFAULT_NOISE).split())
    idx = M.Index(targets, noise)
    ctx = f"Context: {context}\n" if context else ""

    # ---- 1. groups (district names are messy too) ---------------------------------------------------
    gmap = {}
    if use_groups and any(r.get("group") for r in rows) and any(t.get("group") for t in targets):
        tgroups = sorted({t["group"] for t in targets if t.get("group")})
        gmap, ginfo, gidx = M.match_groups(sorted({r["group"] for r in rows if r.get("group")}), tgroups, noise)
        missing = [g for g, v in gmap.items() if v is None]
        log(f"Groups: {len(gmap) - len(missing)}/{len(gmap)} matched by rules"
            + (f"; unmatched: {', '.join(missing)}" if missing else ""))
        if missing and llm:
            items = [{"row": n + 1, "name": g, "candidates": [{"n": j + 1, "name": tgroups[j]} for j in range(len(tgroups))]}
                     for n, g in enumerate(missing)]
            ans = llm.ask(CHOOSE_SYSTEM, ctx + "These are group (e.g. district) names.\n" + _items_json(items), CHOOSE_SCHEMA)
            for a in ans.get("results", []):
                try:
                    g, c = missing[int(a["row"]) - 1], int(a.get("choice") or 0)
                except (ValueError, IndexError, KeyError, TypeError):
                    continue
                if 0 < c <= len(tgroups) and float(a.get("confidence", 0)) >= ai_min_conf:
                    gmap[g] = tgroups[c - 1]
                    log(f"  AI: group '{g}' -> '{tgroups[c - 1]}' ({a.get('reason', '')})")

    # ---- 2. rules ---------------------------------------------------------------------------------------
    res = []
    for n, r in enumerate(rows):
        if feedback and feedback.isCanceled():
            break
        g = gmap.get(r.get("group")) if gmap else None
        d = M.decide(r["name"], g, idx, auto_accept=auto_accept)
        res.append(d)
    todo = [n for n, d in enumerate(res) if d["status"] != "matched"]
    log(f"Rules: {len(rows) - len(todo)}/{len(rows)} rows matched; {len(todo)} need a decision")

    # ---- 3. AI --------------------------------------------------------------------------------------------
    if llm and todo:
        if use_aliases:
            weak = [n for n in todo if res[n]["score"] < 0.9 or res[n]["method"] in ("none", "fuzzy_low")]
            for s in range(0, len(weak), batch):
                part = weak[s:s + batch]
                items = [{"row": n, "name": rows[n]["name"], "group": rows[n].get("group") or ""} for n in part]
                try:
                    ans = llm.ask(ALIAS_SYSTEM, ctx + _items_json(items), ALIAS_SCHEMA)
                except Exception as e:  # noqa: BLE001 - AI is optional, never fail the whole join
                    log(f"  alias step skipped: {e}")
                    break
                for a in ans.get("results", []):
                    try:
                        n = int(a["row"])
                    except (ValueError, KeyError, TypeError):
                        continue
                    if n not in part:
                        continue
                    have = {i for _, i in res[n]["cands"]}  # noqa: C401
                    g = gmap.get(rows[n].get("group")) if gmap else None
                    for alias in (a.get("aliases") or [])[:3]:
                        for grp in ([g, None] if g else [None]):
                            _, _, c = idx.shortlist(str(alias), grp, k=3)
                            for sc, i in c:
                                if i not in have and sc >= 0.8:
                                    res[n]["cands"].append((sc, i))
                                    have.add(i)
                    res[n]["aliases"] = a.get("aliases") or []
        for s in range(0, len(todo), batch):
            if feedback and feedback.isCanceled():
                break
            part = todo[s:s + batch]
            items = []
            for n in part:
                cands = res[n]["cands"][:12]
                res[n]["shown"] = [i for _, i in cands]
                items.append({"row": n, "name": rows[n]["name"], "group": rows[n].get("group") or "",
                              "info": rows[n].get("info", ""),
                              "candidates": [{"n": j + 1, "name": idx.t[i]["name"], "group": idx.t[i].get("group") or "",
                                              "info": idx.t[i].get("extra") or ""} for j, (_, i) in enumerate(cands)]})
            try:
                ans = llm.ask(CHOOSE_SYSTEM, ctx + _items_json(items), CHOOSE_SCHEMA)
            except Exception as e:  # noqa: BLE001
                log(f"  AI step failed for rows {part[0]}..{part[-1]}: {e}")
                continue
            for a in ans.get("results", []):
                try:
                    n, c, conf = int(a["row"]), int(a.get("choice") or 0), float(a.get("confidence") or 0)
                except (ValueError, KeyError, TypeError):
                    continue
                if n not in part:
                    continue
                shown = res[n]["shown"]
                reason = str(a.get("reason", ""))[:200]
                if c == 0:
                    res[n].update(status="none" if conf >= ai_min_conf else "review", method="ai_no_match",
                                  ai_conf=conf, reason="AI: " + reason)
                    res[n].pop("i", None)
                elif 0 < c <= len(shown):
                    pick = idx.t[shown[c - 1]]
                    twins = [i for i in shown if i != shown[c - 1] and idx.t[i]["norm"] == pick["norm"]
                             and idx.t[i].get("group") == pick.get("group") and idx.t[i].get("extra") == pick.get("extra")]
                    # small models sometimes explain one candidate and return another's number
                    low = reason.lower()
                    named = [idx.t[i]["name"] for i in shown if i != shown[c - 1] and len(idx.t[i]["name"]) > 3
                             and idx.t[i]["name"].lower() in low and idx.t[i]["name"].lower() not in pick["name"].lower()]
                    mismatch = bool(named) and pick["name"].lower() not in low
                    ok = conf >= ai_min_conf and not twins and not mismatch
                    if twins:   # nothing in the data can tell these apart; the AI is guessing
                        reason += f" | {len(twins) + 1} identical candidates, check by hand"
                    if mismatch:
                        reason += f" | reason names '{named[0]}' but choice is '{pick['name']}', check by hand"
                    res[n].update(status="matched" if ok else "review", method="ai", i=shown[c - 1],
                                  ai_conf=conf, reason="AI: " + reason)
        log(f"AI: {llm.calls} new call(s) to {llm.provider} {llm.model}".rstrip())

    # ---- 4. package -------------------------------------------------------------------------------------
    out, summary = [], {}
    for r, d in zip(rows, res):
        t = idx.t[d["i"]] if "i" in d else None
        o = {"status": d["status"], "method": d["method"], "score": d.get("score", 0.0),
             "ai_confidence": d.get("ai_conf"), "reason": d.get("reason", ""),
             "target_id": t["id"] if t else None, "target_name": t["name"] if t else None,
             "target_group": t.get("group") if t else None,
             "alternatives": "; ".join(idx.t[i]["name"] + (f" ({idx.t[i]['group']})" if idx.t[i].get("group") else "")
                                       for _, i in d.get("cands", [])[:4] if t is None or i != t["i"])}
        out.append(o)
        summary[d["status"]] = summary.get(d["status"], 0) + 1
    summary["group_map"] = {k: v for k, v in gmap.items()}
    return out, summary
