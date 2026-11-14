"""Debug helper: render pages in a real browser and record every network call they make,
with short JSON response previews. Output: data/probe/<slug>.json

usage: python -m radar.probe URL [URL ...]
"""
import json
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from . import net

OUT = Path(__file__).resolve().parent.parent / "data" / "probe"
SKIP = re.compile(r"\.(png|jpe?g|gif|svg|woff2?|ttf|css|ico|mp4|webp)(\?|$)|google|doubleclick|facebook|linkedin|hotjar|"
                  r"cookielaw|onetrust|usercentrics|cookiebot|clarity|newrelic|nr-data|segment|optimizely|adobedtm|demdex", re.I)


def probe(url):
    calls = []
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(user_agent=net.UA.split(" ai-materials-radar")[0])

        def on_resp(r):
            if SKIP.search(r.url):
                return
            rec = {"url": r.url[:400], "method": r.request.method, "status": r.status,
                   "type": r.headers.get("content-type", "")[:60]}
            if r.request.method == "POST":
                rec["post"] = (r.request.post_data or "")[:800]
            if "json" in rec["type"]:
                try:
                    rec["preview"] = r.text()[:1200]
                except Exception:
                    pass
            calls.append(rec)
        pg.on("response", on_resp)
        err = None
        try:
            pg.goto(url, wait_until="domcontentloaded", timeout=60000)
            pg.wait_for_timeout(8000)
            for _ in range(3):
                pg.mouse.wheel(0, 3000)
                pg.wait_for_timeout(1000)
        except Exception as e:
            err = str(e)[:300]
        try:
            links = pg.eval_on_selector_all("a[href]", "els => els.map(e => [e.href, e.innerText.trim().slice(0,80)])")
        except Exception:
            links = []
        final = pg.url
        b.close()
    joblinks = [l for l in links if re.search(r"job|stelle|vacanc|position|career", l[0], re.I)][:60]
    return {"url": url, "final": final, "error": err, "calls": calls, "job_links": joblinks}


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for u in sys.argv[1:]:
        slug = re.sub(r"[^a-z0-9]+", "-", u.lower())[8:80].strip("-")
        res = probe(u)
        (OUT / f"{slug}.json").write_text(json.dumps(res, indent=1, ensure_ascii=False))
        print(slug, len(res["calls"]), res["error"])
