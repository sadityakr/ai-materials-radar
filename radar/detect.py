"""Detect which hiring system (ATS) a careers page uses, from URLs and page HTML."""
import re
from urllib.parse import urlparse, unquote

# Order matters: more specific / API-friendly systems first.
PATTERNS = [
    ("workday", re.compile(r"https?://([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+)", re.I)),
    ("oracle", re.compile(r"https?://([a-z0-9-]+\.fa\.[a-z0-9-]+\.oraclecloud\.com)/hcmUI/CandidateExperience/[a-z-]+/sites/([A-Za-z0-9_]+)", re.I)),
    ("greenhouse", re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board(?:/js)?\?for=)?([A-Za-z0-9_-]+)", re.I)),
    ("lever", re.compile(r"jobs\.(eu\.)?lever\.co/([A-Za-z0-9_.-]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)", re.I)),
    ("personio", re.compile(r"([a-z0-9-]+)\.jobs\.personio\.(de|com)", re.I)),
    ("smartrecruiters", re.compile(r"(?:careers|jobs)\.smartrecruiters\.com/([A-Za-z0-9_-]+)", re.I)),
    ("recruitee", re.compile(r"([a-z0-9-]+)\.recruitee\.com", re.I)),
    ("workable", re.compile(r"apply\.workable\.com/([a-z0-9_-]+)", re.I)),
    ("bamboohr", re.compile(r"([a-z0-9-]+)\.bamboohr\.com", re.I)),
    ("dvinci", re.compile(r"([a-z0-9-]+\.dvinci-hr\.com)", re.I)),
    ("eightfold", re.compile(r"([a-z0-9-]+)\.eightfold\.ai", re.I)),
    ("successfactors", re.compile(r"(career\d*\.successfactors\.(?:eu|com))/career\?company=([A-Za-z0-9_]+)", re.I)),
    ("join", re.compile(r"join\.com/companies/([a-z0-9_-]+)", re.I)),
    ("teamtailor", re.compile(r"([a-z0-9-]+)\.teamtailor\.com", re.I)),
    ("softgarden", re.compile(r"([a-z0-9-]+)\.softgarden\.io", re.I)),
    ("icims", re.compile(r"((?:careers-)?[a-z0-9-]+)\.icims\.com", re.I)),
    ("taleo", re.compile(r"([a-z0-9-]+)\.taleo\.net", re.I)),
]
# Platform fingerprints on custom domains (no tenant in URL).
FINGERPRINTS = [
    ("sf_csb", re.compile(r"rmkcdn\.successfactors\.com|career-site-builder|j2w\.", re.I)),
    ("phenom", re.compile(r"phenompeople|cdn\.phenom", re.I)),
    ("eightfold_custom", re.compile(r"static\.vscdn\.net|eightfold\.ai/", re.I)),
    ("teamtailor_custom", re.compile(r"teamtailor-cdn|assets\.teamtailor", re.I)),
    ("softgarden_custom", re.compile(r"softgarden", re.I)),
    ("rexx", re.compile(r"rexx-systems\.com|rexx-recruitment", re.I)),
    ("sf_csb", re.compile(r'href="[^"]*/job/[^"/]+/\d{8,}/?"|jobTitle-link', re.I)),
    ("oracle_custom", re.compile(r"/hcmUI/CandidateExperience|CandidateExperience/[a-z]{2}/sites/", re.I)),
]
BAD_TOKENS = {"embed", "js", "v1", "api", "wday", "static", "assets", "www", "app", "careers", "jobs", "cdn", "images"}


def detect(url, html=""):
    """Return list of (type, params) candidates, best first."""
    found = []
    seen = set()
    texts = [url or "", html or ""]
    for text in texts:
        for typ, rx in PATTERNS:
            for m in rx.finditer(text):
                p = _params(typ, m)
                if not p:
                    continue
                key = (typ, tuple(sorted(p.items())))
                if key in seen:
                    continue
                seen.add(key)
                found.append((typ, p))
    host = urlparse(url).netloc if url else ""
    m = re.search(r"/(?:hcmUI/CandidateExperience/[a-z-]+/)?sites/([A-Za-z0-9_]+)", url or "")
    if m and "oraclecloud" not in host:
        found.append(("oracle", {"host": host, "site": m.group(1)}))
    if re.search(r"/job/[^/]+/\d{8,}/?", url or ""):
        found.append(("sf_csb", {"base": f"{urlparse(url).scheme}://{host}"}))
    for typ, rx in FINGERPRINTS:
        if typ == "oracle_custom":
            continue
        if rx.search(html or ""):
            key = (typ, host)
            if key not in seen:
                seen.add(key)
                found.append((typ, {"base": f"{urlparse(url).scheme}://{host}"}))
    return found


def _params(typ, m):
    g = m.groups()
    if typ == "workday":
        tenant, wd, site = g
        if site.lower() in BAD_TOKENS:
            return None
        return {"tenant": tenant.lower(), "wd": wd.lower(), "site": site}
    if typ == "oracle":
        return {"host": g[0].lower(), "site": g[1]}
    if typ == "greenhouse":
        return None if g[0].lower() in BAD_TOKENS else {"token": g[0]}
    if typ == "lever":
        return None if g[1].lower() in BAD_TOKENS else {"company": g[1], "eu": bool(g[0])}
    if typ == "ashby":
        t = unquote(g[0])
        return None if t.lower() in BAD_TOKENS else {"org": t}
    if typ == "personio":
        return None if g[0].lower() in BAD_TOKENS else {"sub": g[0].lower(), "tld": g[1].lower()}
    if typ == "smartrecruiters":
        return None if g[0].lower() in BAD_TOKENS else {"company": g[0]}
    if typ in ("recruitee", "bamboohr", "eightfold", "teamtailor", "softgarden", "taleo"):
        return None if g[0].lower() in BAD_TOKENS else {"sub": g[0].lower()}
    if typ == "workable":
        return {"sub": g[0].lower()}
    if typ == "dvinci":
        return {"host": g[0].lower()}
    if typ == "successfactors":
        return {"host": g[0].lower(), "company": g[1]}
    if typ == "join":
        return {"slug": g[0].lower()}
    if typ == "icims":
        return None if g[0].lower() in BAD_TOKENS else {"sub": g[0].lower()}
    return None
