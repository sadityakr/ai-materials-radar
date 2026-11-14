"""Find and verify the job source for every employer.

For each employer: fetch its careers page (and a few job-list links on it), detect the hiring
system, then actually run the matching reader. The first reader that returns postings wins.
Writes data/sources.json. Manual pins in registry/overrides.json are respected and not touched.

usage: python -m radar.discover [--only id1,id2] [--render]
"""
import argparse
import json
import re
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from . import net
from .detect import detect
from .readers import READERS, GENERIC_ALIASES

ROOT = Path(__file__).resolve().parent.parent
EMP = ROOT / "registry" / "employers.json"
OVR = ROOT / "registry" / "overrides.json"
OUT = ROOT / "data" / "sources.json"

LIST_LINK = re.compile(r"(open (positions|roles|jobs)|current (openings|vacancies)|job (openings|offers|search)|all jobs|"
                       r"vacancies|stellenangebote|offene stellen|stellenbörse|jobbörse|jobs? (finden|suchen)|"
                       r"search jobs|view (all )?jobs|see (all )?jobs|explore jobs|find (a )?jobs?|career opportunities|"
                       r"offres d.emploi|vacatures|社会招聘|校园招聘)", re.I)


def domain_of(url):
    h = urlparse(url).netloc.lower()
    return h[4:] if h.startswith("www.") else h


def fetch(url):
    try:
        r = net.get(url)
        return {"url": url, "final": r.url, "status": r.status_code, "html": r.text if r.status_code == 200 else ""}
    except Exception as e:
        return {"url": url, "final": url, "status": None, "error": str(e)[:200], "html": ""}


def subpage_links(page):
    if not page["html"]:
        return []
    soup = BeautifulSoup(page["html"], "html.parser")
    out = []
    for tag in soup.find_all(["a", "iframe"]):
        href = tag.get("href") or tag.get("src")
        if not href:
            continue
        text = tag.get_text(" ", strip=True) if tag.name == "a" else "iframe"
        if tag.name == "iframe" or LIST_LINK.search(text or ""):
            u = urljoin(page["final"], href)
            if u.startswith("http") and u not in out:
                out.append(u)
    return out[:4]


def try_reader(typ, params):
    fn = READERS[typ][0]
    try:
        jobs = fn(params)
        return {"ok": True, "count": len(jobs), "sample": [j["title"] for j in jobs[:5]]}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}


def discover_one(emp, render=False):
    urls = [u for u in [emp.get("careers_url")] + emp.get("hints", []) if u]
    if emp.get("website"):
        urls.append(emp["website"])
    pages, seen = [], set()
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        pages.append(fetch(u))
    for pg in list(pages[:2]):
        for u in subpage_links(pg):
            if u not in seen:
                seen.add(u)
                pages.append(fetch(u))
    cands = []
    for pg in pages:
        for typ, params in detect(pg["final"], pg["html"]):
            if typ == "eightfold":
                params = {**params, "domain": domain_of(emp.get("website") or emp.get("careers_url"))}
            elif typ == "eightfold_custom":
                typ, params = "eightfold", {"base": params["base"], "domain": domain_of(emp.get("website") or params["base"])}
            if typ in GENERIC_ALIASES:
                typ, params = "generic", {"url": pg["final"], "via": typ}
            if (typ, json.dumps(params, sort_keys=True)) not in [(c[0], json.dumps(c[1], sort_keys=True)) for c in cands]:
                cands.append((typ, params))
    # generic fallbacks on each fetched page that loaded
    for pg in pages:
        if pg["status"] == 200:
            cands.append(("generic", {"url": pg["final"]}))
    tried, chosen = [], None
    for typ, params in cands:
        res = try_reader(typ, params)
        tried.append({"type": typ, "params": params, **res})
        if res["ok"] and res["count"] > 0:
            # a generic page with only 1-2 links is weak evidence; keep looking for better
            if typ.startswith("generic") and res["count"] < 3:
                continue
            chosen = {"type": typ, "params": params}
            break
    if not chosen and render and emp.get("careers_url"):
        params = {"url": emp["careers_url"]}
        res = try_reader("generic_render", params)
        tried.append({"type": "generic_render", "params": params, **res})
        if res["ok"] and res["count"] > 0:
            chosen = {"type": "generic_render", "params": params}
    if not chosen:
        weak = next((t for t in tried if t["ok"] and t["count"] > 0), None)
        if weak:
            chosen = {"type": weak["type"], "params": weak["params"]}
    return {
        "chosen": chosen,
        "count": next((t["count"] for t in tried if chosen and t["type"] == chosen["type"] and t["params"] == chosen["params"]), 0),
        "pages": [{k: p.get(k) for k in ("url", "final", "status", "error")} for p in pages],
        "tried": tried,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--render", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    emps = json.loads(EMP.read_text())
    overrides = json.loads(OVR.read_text()) if OVR.exists() else {}
    only = set(filter(None, a.only.split(",")))
    old = json.loads(OUT.read_text()) if OUT.exists() else {}
    todo = [e for e in emps if (not only or e["id"] in only) and e["id"] not in overrides]

    def work(e):
        try:
            return e["id"], discover_one(e, a.render)
        except Exception:
            return e["id"], {"chosen": None, "count": 0, "error": traceback.format_exc()[-500:]}

    with ThreadPoolExecutor(a.workers) as ex:
        for eid, res in ex.map(work, todo):
            old[eid] = res
            c = res.get("chosen")
            print(f"{eid:45s} {(c or {}).get('type', '-'):16s} {res.get('count', 0)}", flush=True)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(dict(sorted(old.items())), indent=1, ensure_ascii=False))
    n = sum(1 for e in emps if (old.get(e["id"]) or {}).get("chosen") or e["id"] in overrides)
    print(f"\nsources found: {n}/{len(emps)}")


if __name__ == "__main__":
    sys.exit(main())
