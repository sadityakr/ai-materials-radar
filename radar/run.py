"""Read every employer's job source, update state with safe open/close rules, pre-filter, report.

Close rules (the part that must never guess):
- A posting is matched by the employer's own job ID.
- If the source fetch fails, nothing for that employer changes (it is flagged instead).
- If a source that had >= 3 postings suddenly returns 0, that is treated as a failure ("suspect_zero")
  until it happens CONFIRM_ZERO runs in a row.
- A posting is closed only after it is missing from MISSES_TO_CLOSE consecutive successful reads
  (more for the generic page reader, which is noisier).
- A closed posting that reappears is reopened.

usage: python -m radar.run [--only id1,id2] [--no-details]
"""
import argparse
import datetime as dt
import json
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .filters import title_pass, desc_pass
from .readers import READERS

ROOT = Path(__file__).resolve().parent.parent
EMP = ROOT / "registry" / "employers.json"
OVR = ROOT / "registry" / "overrides.json"
SRC = ROOT / "data" / "sources.json"
STATE = ROOT / "data" / "state.json"
CAND = ROOT / "data" / "candidates.json"
HEALTH = ROOT / "data" / "health.json"
REPORT = ROOT / "data" / "report.md"
CHANGES = ROOT / "data" / "changes"

MISSES_TO_CLOSE = 2
MISSES_TO_CLOSE_GENERIC = 3
CONFIRM_ZERO = 3
MAX_DETAILS_PER_EMPLOYER = 150


def now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load(p, default):
    return json.loads(p.read_text()) if p.exists() else default


def source_for(eid, sources, overrides):
    if eid in overrides:
        return overrides[eid] if overrides[eid].get("type") != "none" else None
    return (sources.get(eid) or {}).get("chosen")


def read_one(src):
    fn = READERS[src["type"]][0]
    try:
        return {"ok": True, "jobs": fn(src["params"])}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}", "trace": traceback.format_exc()[-400:]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--no-details", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()

    T = now()
    emps = load(EMP, [])
    sources, overrides = load(SRC, {}), load(OVR, {})
    state = load(STATE, {"jobs": {}, "sources": {}})
    only = set(filter(None, a.only.split(",")))
    todo = [(e, source_for(e["id"], sources, overrides)) for e in emps if not only or e["id"] in only]

    def work(item):
        e, src = item
        return e, src, (read_one(src) if src else None)

    opened, closed, reopened = [], [], []
    health = load(HEALTH, {})
    with ThreadPoolExecutor(a.workers) as ex:
        results = list(ex.map(work, todo))

    for e, src, res in results:
        eid = e["id"]
        ss = state["sources"].setdefault(eid, {"last_ok": None, "fails": 0, "zero_runs": 0, "last_count": 0})
        h = {"name": e["name"], "type": (src or {}).get("type"), "checked": T}
        if not src:
            h.update(status="no_source")
            health[eid] = h
            continue
        if not res["ok"]:
            ss["fails"] += 1
            h.update(status="failed", error=res["error"], fails_in_row=ss["fails"], last_ok=ss["last_ok"])
            health[eid] = h
            continue
        jobs = res["jobs"]
        if src.get("location"):
            import re as _re
            rx = _re.compile(src["location"], _re.I)
            jobs = [j for j in jobs if not j.get("location") or rx.search(j.get("location", ""))]
        # dedupe by job id
        jobs = list({j["job_id"]: j for j in jobs if j.get("job_id") and j.get("title")}.values())
        if not jobs and ss["last_count"] >= 3:
            ss["zero_runs"] += 1
            if ss["zero_runs"] < CONFIRM_ZERO:
                h.update(status="suspect_zero", zero_runs=ss["zero_runs"], last_count=ss["last_count"], last_ok=ss["last_ok"])
                health[eid] = h
                continue
        ss.update(last_ok=T, fails=0, zero_runs=0, last_count=len(jobs))
        h.update(status="ok", count=len(jobs))
        health[eid] = h

        generic = src["type"].startswith("generic")
        limit = MISSES_TO_CLOSE_GENERIC if generic else MISSES_TO_CLOSE
        seen = set()
        for j in jobs:
            key = f"{eid}::{j['job_id']}"
            seen.add(key)
            rec = state["jobs"].get(key)
            if rec is None:
                rec = {"employer": eid, "job_id": j["job_id"], "first_seen": T, "status": "open"}
                state["jobs"][key] = rec
                opened.append(key)
            elif rec["status"] == "closed":
                rec["status"] = "open"
                rec.pop("closed_on", None)
                reopened.append(key)
            rec.update(title=j["title"], url=j.get("url", ""), location=j.get("location", ""), posted=j.get("posted", ""),
                       last_seen=T, misses=0, source=src["type"])
            if j.get("desc") and not rec.get("desc"):
                rec["desc"] = j["desc"][:3000]
            if j.get("_path"):
                rec["_path"] = j["_path"]
            if j.get("_pid"):
                rec["_pid"] = j["_pid"]
        for key, rec in state["jobs"].items():
            if rec["employer"] == eid and rec["status"] == "open" and key not in seen:
                rec["misses"] = rec.get("misses", 0) + 1
                if rec["misses"] >= limit:
                    rec["status"] = "closed"
                    rec["closed_on"] = T
                    closed.append(key)

        # descriptions for title-passing postings that have none yet
        if not a.no_details:
            det = READERS[src["type"]][1]
            n = 0
            for key in seen:
                rec = state["jobs"][key]
                if rec.get("desc") is not None or not title_pass(rec["title"]) or det is None:
                    continue
                if n >= MAX_DETAILS_PER_EMPLOYER:
                    break
                try:
                    rec["desc"] = det(src["params"], {**rec}) or ""
                except Exception as ex_:
                    rec["desc_error"] = str(ex_)[:120]
                n += 1

    # candidates: open postings that pass both filters
    cands = []
    names = {e["id"]: e["name"] for e in emps}
    for key, rec in state["jobs"].items():
        if rec["status"] != "open" or not title_pass(rec["title"]):
            continue
        if not desc_pass(rec["title"], rec.get("desc")):
            continue
        cands.append({"key": key, "employer": rec["employer"], "employer_name": names.get(rec["employer"], rec["employer"]),
                      "title": rec["title"], "url": rec.get("url", ""), "location": rec.get("location", ""),
                      "first_seen": rec["first_seen"], "last_seen": rec["last_seen"], "has_desc": bool(rec.get("desc")),
                      "desc": (rec.get("desc") or "")[:1500]})
    cands.sort(key=lambda c: (c["employer"], c["title"]))

    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state, indent=0, ensure_ascii=False))
    CAND.write_text(json.dumps({"generated": T, "count": len(cands), "candidates": cands}, indent=1, ensure_ascii=False))
    HEALTH.write_text(json.dumps(dict(sorted(health.items())), indent=1, ensure_ascii=False))
    CHANGES.mkdir(exist_ok=True)
    (CHANGES / f"{T[:10]}.json").write_text(json.dumps({"run": T, "opened": opened, "closed": closed, "reopened": reopened}, indent=1))
    write_report(T, emps, health, state, cands, opened, closed, reopened)
    print(f"ok={sum(h.get('status') == 'ok' for h in health.values())} failed={sum(h.get('status') == 'failed' for h in health.values())} "
          f"no_source={sum(h.get('status') == 'no_source' for h in health.values())} opened={len(opened)} closed={len(closed)} candidates={len(cands)}")


def write_report(T, emps, health, state, cands, opened, closed, reopened):
    from collections import Counter
    st = Counter(h.get("status") for h in health.values())
    ty = Counter(h.get("type") for h in health.values() if h.get("status") == "ok")
    lines = [f"# Run {T}", "",
             f"Employers: {len(emps)}. Read OK: {st['ok']}. Failed: {st['failed']}. Suspect zero: {st['suspect_zero']}. No source: {st['no_source']}.",
             f"Open postings tracked: {sum(r['status'] == 'open' for r in state['jobs'].values())}. "
             f"New this run: {len(opened)}. Closed this run: {len(closed)}. Reopened: {len(reopened)}. Candidates after pre-filter: {len(cands)}.",
             "", "Readers in use: " + ", ".join(f"{k} {v}" for k, v in ty.most_common()), "",
             "| Employer | System | Status | Postings | Candidates | Note |", "|---|---|---|---|---|---|"]
    cc = Counter(c["employer"] for c in cands)
    for e in sorted(emps, key=lambda e: e["name"].lower()):
        h = health.get(e["id"], {})
        lines.append(f"| {e['name']} | {h.get('type') or '-'} | {h.get('status', '-')} | {h.get('count', '')} | {cc.get(e['id'], '')} | "
                     f"{(h.get('error') or '')[:80].replace('|', '/')} |")
    REPORT.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
