"""One reader per hiring system. Each returns a list of postings:
{"job_id", "title", "location", "url", "posted", "desc"(optional)}
and raises on failure (so a failed fetch is never mistaken for "no jobs").
`detail(source, job)` fetches the description for one posting when the list call has none.
"""
import html as htmllib
import json
import re
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from . import net

MAX_JOBS = 4000


class ReaderError(Exception):
    pass


def _clean(s, n=None):
    if not s:
        return ""
    s = BeautifulSoup(htmllib.unescape(str(s)), "html.parser").get_text(" ")
    s = re.sub(r"\s+", " ", s).strip()
    return s[:n] if n else s


def _json(resp):
    if resp.status_code != 200:
        raise ReaderError(f"HTTP {resp.status_code} {resp.url}")
    try:
        return resp.json()
    except ValueError:
        raise ReaderError(f"not JSON from {resp.url}")


# ---------------------------------------------------------------- Workday
def workday(p):
    host = f"https://{p['tenant']}.{p['wd']}.myworkdayjobs.com"
    api = f"{host}/wday/cxs/{p['tenant']}/{p['site']}/jobs"
    post = lambda body: _json(net.post(api, json=body, headers={"Accept": "application/json"}))
    try:
        return _workday(p, host, post)
    except Exception as e:
        if not _blocked(e):
            raise
    bf = BrowserFetcher(f"{host}/{p['site']}")
    try:
        return _workday(p, host, lambda body: bf.json(api, "POST", body))
    finally:
        bf.close()


def _workday(p, host, post):
    jobs, offset, total = [], 0, None
    while True:
        d = post({"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": ""})
        if total is None:
            total = d.get("total", 0)
        page = d.get("jobPostings") or []
        for j in page:
            path = j.get("externalPath") or ""
            jid = (j.get("bulletFields") or [None])[0] or path
            jobs.append({"job_id": str(jid), "title": j.get("title", ""), "location": j.get("locationsText", ""),
                         "url": f"{host}/{p['site']}{path}", "posted": j.get("postedOn", ""), "_path": path})
        offset += 20
        if not page or offset >= total or len(jobs) >= MAX_JOBS:
            break
    return jobs


def workday_detail(p, job):
    host = f"https://{p['tenant']}.{p['wd']}.myworkdayjobs.com"
    d = _json(net.get(f"{host}/wday/cxs/{p['tenant']}/{p['site']}{job['_path']}", headers={"Accept": "application/json"}))
    return _clean((d.get("jobPostingInfo") or {}).get("jobDescription"), 3000)


# ---------------------------------------------------------------- Oracle Cloud HCM
def oracle(p):
    jobs, offset = [], 0
    while True:
        url = (f"https://{p['host']}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true"
               f"&expand=requisitionList.secondaryLocations&finder=findReqs;siteNumber={p['site']},limit=200,offset={offset}")
        d = _json(net.get(url))
        item = (d.get("items") or [{}])[0]
        page = item.get("requisitionList") or []
        for j in page:
            jobs.append({"job_id": str(j.get("Id")), "title": j.get("Title", ""), "location": j.get("PrimaryLocation", ""),
                         "url": f"https://{p['host']}/hcmUI/CandidateExperience/en/sites/{p['site']}/job/{j.get('Id')}",
                         "posted": j.get("PostedDate", ""), "desc": _clean(j.get("ShortDescriptionStr"), 3000)})
        offset += 200
        if not page or offset >= (item.get("TotalJobsCount") or 0) or len(jobs) >= MAX_JOBS:
            break
    return jobs


# ---------------------------------------------------------------- Greenhouse
def greenhouse(p):
    d = _json(net.get(f"https://boards-api.greenhouse.io/v1/boards/{p['token']}/jobs?content=true"))
    return [{"job_id": str(j["id"]), "title": j.get("title", ""), "location": (j.get("location") or {}).get("name", ""),
             "url": j.get("absolute_url", ""), "posted": j.get("updated_at", ""), "desc": _clean(j.get("content"), 3000)}
            for j in d.get("jobs", [])]


# ---------------------------------------------------------------- Lever
def lever(p):
    base = "https://api.eu.lever.co" if p.get("eu") else "https://api.lever.co"
    d = _json(net.get(f"{base}/v0/postings/{p['company']}?mode=json"))
    if not isinstance(d, list):
        raise ReaderError("unexpected Lever response")
    return [{"job_id": j["id"], "title": j.get("text", ""), "location": (j.get("categories") or {}).get("location", ""),
             "url": j.get("hostedUrl", ""), "posted": str(j.get("createdAt", "")), "desc": (j.get("descriptionPlain") or "")[:3000]}
            for j in d]


# ---------------------------------------------------------------- Ashby
def ashby(p):
    d = _json(net.get(f"https://api.ashbyhq.com/posting-api/job-board/{p['org']}?includeCompensation=false"))
    return [{"job_id": j["id"], "title": j.get("title", ""), "location": j.get("location", ""),
             "url": j.get("jobUrl", ""), "posted": j.get("publishedAt", ""), "desc": (j.get("descriptionPlain") or "")[:3000]}
            for j in d.get("jobs", [])]


# ---------------------------------------------------------------- Personio
def personio(p):
    errs = []
    for tld in [p["tld"], "de" if p["tld"] == "com" else "com"]:
        base = f"https://{p['sub']}.jobs.personio.{tld}"
        r = net.get(f"{base}/xml?language=en")
        if r.status_code == 200 and b"<workzag-jobs" in r.content[:500]:
            return _personio_xml(base, r)
        errs.append(f"xml {r.status_code}")
        r = net.get(f"{base}/search.json")
        if r.status_code == 200:
            try:
                d = r.json()
                return [{"job_id": str(j["id"]), "title": j.get("name", ""), "location": j.get("office", ""),
                         "url": f"{base}/job/{j['id']}", "posted": ""} for j in d]
            except Exception:
                pass
        errs.append(f"search.json {r.status_code}")
        r = net.get(f"{base}/?language=en")
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            out = {}
            for a in soup.select("a[href*='/job/']"):
                m = re.search(r"/job/(\d+)", a["href"])
                if m:
                    t = a.select_one("h1,h2,h3,h4,[class*=title]")
                    out[m.group(1)] = {"job_id": m.group(1), "title": (t or a).get_text(" ", strip=True)[:160],
                                       "location": "", "url": f"{base}/job/{m.group(1)}", "posted": ""}
            return list(out.values())
        errs.append(f"page {r.status_code}")
    raise ReaderError("Personio: " + ", ".join(errs))


def _personio_xml(base, r):
    root = ET.fromstring(r.content)
    jobs = []
    for pos in root.iter("position"):
        jid = pos.findtext("id")
        desc = " ".join(_clean(v.text) for v in pos.iter("value"))
        jobs.append({"job_id": jid, "title": pos.findtext("name") or "", "location": pos.findtext("office") or "",
                     "url": f"{base}/job/{jid}", "posted": pos.findtext("createdAt") or "", "desc": desc[:3000]})
    return jobs


# ---------------------------------------------------------------- SmartRecruiters
def smartrecruiters(p):
    jobs, offset = [], 0
    while True:
        d = _json(net.get(f"https://api.smartrecruiters.com/v1/companies/{p['company']}/postings?limit=100&offset={offset}"))
        page = d.get("content") or []
        for j in page:
            loc = j.get("location") or {}
            jobs.append({"job_id": str(j["id"]), "title": j.get("name", ""),
                         "location": ", ".join(x for x in [loc.get("city"), loc.get("country")] if x),
                         "url": f"https://jobs.smartrecruiters.com/{p['company']}/{j['id']}", "posted": j.get("releasedDate", "")})
        offset += 100
        if not page or offset >= d.get("totalFound", 0) or len(jobs) >= MAX_JOBS:
            break
    return jobs


def smartrecruiters_detail(p, job):
    d = _json(net.get(f"https://api.smartrecruiters.com/v1/companies/{p['company']}/postings/{job['job_id']}"))
    secs = ((d.get("jobAd") or {}).get("sections") or {})
    return _clean(" ".join((secs.get(k) or {}).get("text", "") for k in ("jobDescription", "qualifications")), 3000)


# ---------------------------------------------------------------- Recruitee
def recruitee(p):
    d = _json(net.get(f"https://{p['sub']}.recruitee.com/api/offers/"))
    return [{"job_id": str(j["id"]), "title": j.get("title", ""), "location": j.get("location", ""),
             "url": j.get("careers_url", ""), "posted": j.get("published_at", ""),
             "desc": _clean((j.get("description") or "") + " " + (j.get("requirements") or ""), 3000)}
            for j in d.get("offers", [])]


# ---------------------------------------------------------------- Workable
def workable(p):
    jobs, token = [], None
    while True:
        body = {"query": "", "location": [], "department": [], "worktype": [], "remote": []}
        if token:
            body["token"] = token
        d = _json(net.post(f"https://apply.workable.com/api/v3/accounts/{p['sub']}/jobs", json=body))
        for j in d.get("results", []):
            loc = j.get("location") or {}
            jobs.append({"job_id": j.get("shortcode") or str(j.get("id")), "title": j.get("title", ""),
                         "location": ", ".join(x for x in [loc.get("city"), loc.get("country")] if x),
                         "url": f"https://apply.workable.com/{p['sub']}/j/{j.get('shortcode')}/", "posted": j.get("published", "")})
        token = d.get("nextPage")
        if not token or len(jobs) >= MAX_JOBS:
            break
    return jobs


# ---------------------------------------------------------------- BambooHR
def bamboohr(p):
    d = _json(net.get(f"https://{p['sub']}.bamboohr.com/careers/list", headers={"Accept": "application/json"}))
    return [{"job_id": str(j["id"]), "title": j.get("jobOpeningName", ""),
             "location": ", ".join(x for x in [(j.get("location") or {}).get("city"), (j.get("location") or {}).get("state")] if x),
             "url": f"https://{p['sub']}.bamboohr.com/careers/{j['id']}", "posted": ""}
            for j in d.get("result", [])]


# ---------------------------------------------------------------- d.vinci
def dvinci(p):
    d = _json(net.get(f"https://{p['host']}/jobPublication/list.json"))
    out = []
    for j in d if isinstance(d, list) else d.get("jobPublications", []):
        title = j.get("position") or (j.get("jobOpening") or {}).get("name") or j.get("title") or ""
        out.append({"job_id": str(j.get("id")), "title": title, "location": j.get("location", "") if isinstance(j.get("location"), str) else "",
                    "url": j.get("jobPublicationURL") or j.get("url") or "", "posted": j.get("startDate", "")})
    return out


# ---------------------------------------------------------------- browser-context fetch
class BrowserFetcher:
    """Opens a real page once, then calls JSON APIs from inside it (same origin, real cookies).
    Used when a site's bot protection rejects plain HTTP clients."""

    def __init__(self, page_url):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._b = self._pw.chromium.launch()
        self.page = self._b.new_page(user_agent=net.UA.split(" ai-materials-radar")[0])
        self.page.goto(page_url, wait_until="domcontentloaded", timeout=60000)
        self.page.wait_for_timeout(3000)

    def json(self, url, method="GET", body=None):
        r = self.page.evaluate("""async ([url, method, body]) => {
            const o = {method, headers: {'Accept': 'application/json'}, credentials: 'include'};
            if (body) { o.body = JSON.stringify(body); o.headers['Content-Type'] = 'application/json'; }
            const r = await fetch(url, o); const t = await r.text(); return [r.status, t];
        }""", [url, method, body])
        if r[0] != 200:
            raise ReaderError(f"HTTP {r[0]} (browser) {url}")
        try:
            return json.loads(r[1])
        except ValueError:
            raise ReaderError(f"not JSON (browser) {url}")

    def close(self):
        try:
            self._b.close()
            self._pw.stop()
        except Exception:
            pass


def _blocked(e):
    return isinstance(e, ReaderError) and re.search(r"HTTP (401|403|406|429|5\d\d)", str(e))


# ---------------------------------------------------------------- Eightfold
def eightfold(p):
    base = p.get("base") or f"https://{p['sub']}.eightfold.ai"
    try:
        return _eightfold(p, base, lambda u: _json(net.get(u, headers={"Accept": "application/json"})))
    except Exception as e:
        if not _blocked(e) and "404" not in str(e):
            raise
    bf = BrowserFetcher(f"{base}/careers")
    try:
        return _eightfold(p, base, bf.json)
    finally:
        bf.close()


def _eightfold(p, base, getj):
    try:
        return _eightfold_v2(p, base, getj)
    except ReaderError as e:
        if "404" not in str(e):
            raise
        return _eightfold_pcsx(p, base, getj)


def _eightfold_pcsx(p, base, getj):
    jobs, start = [], 0
    while True:
        d = getj(f"{base}/api/pcsx/search?domain={p['domain']}&query=&location=&start={start}&sort_by=timestamp")
        data = d.get("data") or {}
        page = data.get("positions") or []
        for j in page:
            locs = j.get("standardizedLocations") or j.get("locations") or []
            jobs.append({"job_id": str(j.get("displayJobId") or j.get("id")), "title": j.get("name", ""),
                         "location": "; ".join(locs[:3]) if isinstance(locs, list) else str(locs),
                         "url": urljoin(base, j.get("positionUrl") or f"/careers/job/{j.get('id')}"),
                         "posted": str(j.get("postedTs", "")), "_pid": j.get("id")})
        start += len(page)
        if not page or start >= (data.get("count") or 0) or len(jobs) >= MAX_JOBS:
            break
    return jobs


def _eightfold_v2(p, base, getj):
    jobs, start = [], 0
    while True:
        url = f"{base}/api/apply/v2/jobs?domain={p['domain']}&start={start}&num=10&sort_by=timestamp"
        d = getj(url)
        page = d.get("positions") or []
        for j in page:
            jobs.append({"job_id": str(j.get("display_job_id") or j.get("id")), "title": j.get("name", ""),
                         "location": j.get("location", ""), "url": j.get("canonicalPositionUrl") or f"{base}/careers?pid={j.get('id')}",
                         "posted": str(j.get("t_create", "")), "_pid": j.get("id")})
        start += len(page)
        if not page or start >= d.get("count", 0) or len(jobs) >= MAX_JOBS:
            break
    return jobs


def eightfold_detail(p, job):
    base = p.get("base") or f"https://{p['sub']}.eightfold.ai"
    try:
        d = _json(net.get(f"{base}/api/apply/v2/jobs/{job['_pid']}?domain={p['domain']}", headers={"Accept": "application/json"}))
        return _clean(d.get("job_description"), 3000)
    except Exception:
        return ""  # descriptions are optional; the classifier works from the title and a later page read


# ---------------------------------------------------------------- SuccessFactors Career Site Builder
def sf_csb(p):
    base = p["base"].rstrip("/")
    jobs, start, seen = [], 0, set()
    while True:
        r = net.get(f"{base}/search/?q=&sortColumn=referencedate&sortDirection=desc&startrow={start}")
        if r.status_code != 200:
            raise ReaderError(f"HTTP {r.status_code} {r.url}")
        soup = BeautifulSoup(r.text, "html.parser")
        rows = soup.select("tr.data-row") or soup.select("li.job-tile")
        new = 0
        for row in rows:
            a = row.select_one("a.jobTitle-link") or row.select_one("a[href*='/job/']")
            if not a:
                continue
            href = urljoin(base + "/", a.get("href", ""))
            m = re.search(r"/(\d{6,})/?", href)
            jid = m.group(1) if m else href
            if jid in seen:
                continue
            seen.add(jid)
            new += 1
            loc = row.select_one(".jobLocation") or row.select_one("[class*=location]")
            jobs.append({"job_id": jid, "title": a.get_text(" ", strip=True), "location": loc.get_text(" ", strip=True) if loc else "",
                         "url": href, "posted": ""})
        start += 25
        if new == 0 or len(jobs) >= MAX_JOBS:
            break
    if not jobs:
        raise ReaderError("no rows parsed from SuccessFactors search page")
    return jobs


# ---------------------------------------------------------------- Phenom
def phenom(p):
    base = p["base"].rstrip("/")
    r = net.get(f"{base}/search-results")
    if r.status_code != 200:
        r = net.get(f"{base}/global/en/search-results")
    if r.status_code != 200:
        raise ReaderError(f"HTTP {r.status_code} {r.url}")
    m = re.search(r"phApp\.ddo\s*=\s*(\{.*?\});\s*phApp", r.text, re.S)
    meta = {}
    for k in ("refNum", "locale", "country", "lang"):
        mm = re.search(rf'"{k}"\s*:\s*"([^"]+)"', r.text)
        if mm:
            meta[k] = mm.group(1)
    jobs, frm = [], 0
    total = None
    while True:
        body = {"lang": meta.get("locale", "en_global"), "deviceType": "desktop", "country": meta.get("country", "global"),
                "pageName": "search-results", "ddoKey": "refineSearch", "sortBy": "Most recent", "subsearch": "", "from": frm,
                "jobs": True, "counts": True, "all_fields": [], "size": 50, "clearAll": False, "jdsource": "facets",
                "isSliderEnable": False, "pageId": "page20", "siteType": "external", "keywords": "", "global": True,
                "selected_fields": {}, "locationData": {}, "refNum": meta.get("refNum", "")}
        d = _json(net.post(f"{base}/widgets", json=body))
        rs = d.get("refineSearch") or {}
        data = rs.get("data") or {}
        page = data.get("jobs") or []
        if total is None:
            total = rs.get("totalHits", 0)
        for j in page:
            jid = j.get("jobId") or j.get("reqId") or j.get("jobSeqNo")
            jobs.append({"job_id": str(jid), "title": j.get("title", ""), "location": j.get("location") or j.get("cityStateCountry", ""),
                         "url": j.get("applyUrl") or f"{base}/job/{jid}", "posted": j.get("postedDate", ""),
                         "desc": _clean(j.get("descriptionTeaser"), 1500)})
        frm += 50
        if not page or frm >= (total or 0) or len(jobs) >= MAX_JOBS:
            break
    if not jobs and m is None:
        raise ReaderError("Phenom page without phApp data")
    return jobs


# ---------------------------------------------------------------- iCIMS
def icims(p):
    jobs, page, seen = [], 0, set()
    while page < 40:
        r = net.get(f"https://{p['sub']}.icims.com/jobs/search?ss=1&in_iframe=1&pr={page}")
        if r.status_code != 200:
            raise ReaderError(f"HTTP {r.status_code} {r.url}")
        soup = BeautifulSoup(r.text, "html.parser")
        new = 0
        for a in soup.select("a[href*='/jobs/']"):
            m = re.search(r"/jobs/(\d+)/", a.get("href", ""))
            title = a.get_text(" ", strip=True)
            if not m or m.group(1) in seen or len(title) < 4:
                continue
            seen.add(m.group(1))
            new += 1
            jobs.append({"job_id": m.group(1), "title": re.sub(r"^Title\s*", "", title), "location": "",
                         "url": a["href"].split("?")[0], "posted": ""})
        if new == 0:
            break
        page += 1
    return jobs


# ---------------------------------------------------------------- JOIN
def join(p):
    r = net.get(f"https://join.com/companies/{p['slug']}")
    if r.status_code != 200:
        raise ReaderError(f"HTTP {r.status_code} {r.url}")
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.S)
    if not m:
        raise ReaderError("JOIN page without __NEXT_DATA__")
    out = {}

    def walk(o):
        if isinstance(o, dict):
            if "title" in o and ("idParam" in o or ("id" in o and "workplaceType" in o)):
                jid = str(o.get("idParam") or o.get("id"))
                out[jid] = {"job_id": jid, "title": o["title"], "location": ((o.get("city") or {}).get("cityName") or ""),
                            "url": f"https://join.com/companies/{p['slug']}/{jid}", "posted": o.get("createdAt", "")}
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(json.loads(m.group(1)))
    return list(out.values())


# ---------------------------------------------------------------- Generic HTML page
JOB_HREF = re.compile(r"(/jobs?/|/job-|jobid=|job_id=|/stellen|/stelle/|/vacanc|/vacature|/position|/opening|/offre|/emploi|"
                      r"/requisition|/stellenangebot|/jobdetail|/job_detail|/karriere/.+/.+|/careers?/.+/.+|/career-opportunities/|"
                      r"/zhaopin|/recruit|/social-?recruit|/campus)", re.I)
NAV_TEXT = re.compile(r"^(all jobs|jobs|careers?|karriere|apply( now)?|jetzt bewerben|bewerben|more|mehr|read more|weiterlesen|"
                      r"view all|alle stellen|stellenangebote|open positions|search|suche|back|zurück|details|learn more|"
                      r"job alert|login|sign in|initiativbewerbung|unsolicited application)\b", re.I)


def generic(p, html=None):
    url = p["url"]
    if html is None:
        r = net.get(url)
        if r.status_code != 200:
            raise ReaderError(f"HTTP {r.status_code} {url}")
        html, url = r.text, r.url
    soup = BeautifulSoup(html, "html.parser")
    jobs = {}
    for s in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(s.string or "")
        except Exception:
            continue
        for o in (data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []):
            if isinstance(o, dict) and o.get("@type") == "JobPosting":
                ju = o.get("url") or url
                jobs[ju] = {"job_id": _norm(ju), "title": o.get("title", ""), "location": "", "url": ju,
                            "posted": o.get("datePosted", ""), "desc": _clean(o.get("description"), 3000)}
    host = urlparse(url).netloc
    for a in soup.find_all("a", href=True):
        href = urljoin(url, a["href"])
        text = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
        if not (6 <= len(text) <= 160) or NAV_TEXT.match(text) or not JOB_HREF.search(href):
            continue
        if len(text.split()) < 2 and not re.search(r"[\u4e00-\u9fff]{4,}", text):
            continue
        if not _looks_like_posting(href):
            continue
        if href.rstrip("/") == url.rstrip("/") or href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        if urlparse(href).netloc not in (host, "") and not p.get("allow_offsite"):
            continue
        jobs.setdefault(href, {"job_id": _norm(href), "title": text, "location": "", "url": href, "posted": ""})
    return list(jobs.values())


def _looks_like_posting(href):
    """A posting URL usually ends in an id (4+ digits / uuid) or a multi-word slug, not a section name."""
    pr = urlparse(href)
    last = [x for x in pr.path.split("/") if x]
    last = last[-1] if last else ""
    if re.search(r"\d{4,}|[0-9a-f]{8}-[0-9a-f]{4}", pr.path + "?" + (pr.query or ""), re.I):
        return True
    return len(re.split(r"[-_]+", re.sub(r"\.(html?|aspx?|php)$", "", last))) >= 3


def _norm(u):
    pr = urlparse(u)
    return (pr.netloc + pr.path).rstrip("/").lower() + (("?" + pr.query) if re.search(r"id=", pr.query or "", re.I) else "")


def generic_render(p):
    """Same as generic, but renders JavaScript first (Playwright)."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(user_agent=net.UA)
        pg.goto(p["url"], wait_until="networkidle", timeout=60000)
        for _ in range(p.get("scrolls", 3)):
            pg.mouse.wheel(0, 4000)
            pg.wait_for_timeout(800)
        html, final = pg.content(), pg.url
        b.close()
    return generic({**p, "url": final}, html=html)


def generic_detail(p, job):
    r = net.get(job["url"])
    if r.status_code != 200:
        return ""
    soup = BeautifulSoup(r.text, "html.parser")
    for t in soup(["script", "style", "nav", "header", "footer"]):
        t.decompose()
    return _clean(soup.get_text(" "), 3000)


READERS = {
    "workday": (workday, workday_detail), "oracle": (oracle, None), "greenhouse": (greenhouse, None),
    "lever": (lever, None), "ashby": (ashby, None), "personio": (personio, None),
    "smartrecruiters": (smartrecruiters, smartrecruiters_detail), "recruitee": (recruitee, None),
    "workable": (workable, generic_detail), "bamboohr": (bamboohr, generic_detail), "dvinci": (dvinci, generic_detail),
    "eightfold": (eightfold, eightfold_detail), "sf_csb": (sf_csb, generic_detail), "phenom": (phenom, generic_detail),
    "icims": (icims, generic_detail), "join": (join, generic_detail),
    "generic": (generic, generic_detail), "generic_render": (generic_render, generic_detail),
}
# Systems detected but read through the generic page reader.
GENERIC_ALIASES = {"teamtailor", "teamtailor_custom", "softgarden", "softgarden_custom", "taleo", "rexx", "successfactors"}
