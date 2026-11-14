"""HTTP helpers: one shared session, polite per-host pacing, retries."""
import threading
import time
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0 Safari/537.36 ai-materials-radar/0.1 (personal job search)")
TIMEOUT = 30
_host_lock = threading.Lock()
_host_last = {}
MIN_GAP = 1.0  # seconds between requests to the same host

_local = threading.local()


def session():
    s = getattr(_local, "s", None)
    if s is None:
        s = requests.Session()
        retry = Retry(total=3, backoff_factor=1.5, status_forcelist=(429, 500, 502, 503, 504),
                      allowed_methods=frozenset(["GET", "POST", "HEAD"]))
        s.mount("https://", HTTPAdapter(max_retries=retry))
        s.mount("http://", HTTPAdapter(max_retries=retry))
        s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.8,de;q=0.6"})
        _local.s = s
    return s


def _pace(url):
    host = urlparse(url).netloc
    while True:
        with _host_lock:
            now = time.monotonic()
            last = _host_last.get(host, 0)
            if now - last >= MIN_GAP:
                _host_last[host] = now
                return
            wait = MIN_GAP - (now - last)
        time.sleep(wait)


def get(url, **kw):
    _pace(url)
    kw.setdefault("timeout", TIMEOUT)
    return session().get(url, **kw)


def post(url, **kw):
    _pace(url)
    kw.setdefault("timeout", TIMEOUT)
    return session().post(url, **kw)
