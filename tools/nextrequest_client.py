"""Shared HTTP client and parsers for the City of Berkeley NextRequest portal.

Standard library only. Used by nextrequest_watch.py and ingest_nextrequest.py.

Endpoints (public, no authentication; JSON when sent "Accept: application/json"):
  /client/requests?page_number=N&sort_field=request_date&sort_order=desc
      100 requests per page, newest first. Other query parameters are ignored.
  /client/requests/<id>                     one request (request_text is HTML)
  /client/requests/<id>/timeline?page_number=N
  /client/request_documents?request_id=<id>&page_number=N
  /documents/<doc id>/download              302 to a presigned S3 URL (expires in
                                            ~1000 s); the file itself. The listed
                                            asset_url is not publicly readable (403).
"""
import datetime, hashlib, html, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request

BASE = "https://cityofberkeleyca.nextrequest.com"
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 berkeley-public-records")
# Seconds between requests. The portal answers 429 (with no Retry-After) after roughly
# 40 requests at one per second, so the default is slower than that.
MIN_INTERVAL = 2.5


class NotPublic(Exception):
    """The portal refused access (403/404): the request or document is not public."""


class Client:
    def __init__(self, cache_dir=None, min_interval=MIN_INTERVAL, retries=6, backoff=30.0,
                 opener=None, log=None):
        self.cache_dir = cache_dir
        self.backoff = backoff
        self.log = log or (lambda *a: print(*a, file=sys.stderr))
        self.min_interval = min_interval
        self.retries = retries
        self._last = 0.0
        self._open = opener or urllib.request.urlopen

    def _throttle(self):
        wait = self._last + self.min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def _cache_path(self, url):
        if not self.cache_dir:
            return None
        os.makedirs(self.cache_dir, exist_ok=True)
        return os.path.join(self.cache_dir, hashlib.sha256(url.encode()).hexdigest())

    def get(self, url, accept="application/json", use_cache=True):
        """GET url and return bytes, following redirects. Raises NotPublic on 403/404."""
        cp = self._cache_path(url) if use_cache else None
        if cp and os.path.exists(cp):
            with open(cp, "rb") as f:
                return f.read()
        req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": USER_AGENT})
        delay = self.backoff
        for attempt in range(self.retries + 1):
            self._throttle()
            try:
                with self._open(req, timeout=120) as r:
                    body = r.read()
                break
            except urllib.error.HTTPError as e:
                if e.code in (403, 404):
                    raise NotPublic(f"{e.code} {url}") from None
                if e.code not in (429, 500, 502, 503, 504) or attempt == self.retries:
                    raise
                wait = delay
                ra = e.headers.get("Retry-After") if e.headers else None
                if ra and ra.isdigit():
                    wait = max(wait, int(ra))
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                if attempt == self.retries:
                    raise
                wait = delay
            self.log(f"  retrying in {wait:.0f}s: {url}")
            time.sleep(wait)
            delay = min(delay * 2, 600)
        if cp:
            with open(cp, "wb") as f:
                f.write(body)
        return body

    def get_json(self, path, **params):
        url = BASE + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        return json.loads(self.get(url))

    # --- endpoints -------------------------------------------------------------
    def list_page(self, n):
        return self.get_json("/client/requests", page_number=n,
                             sort_field="request_date", sort_order="desc")

    def departments(self):
        return [d["name"] for d in self.get_json("/client/departments")]

    def request(self, rid):
        return self.get_json(f"/client/requests/{rid}")

    def timeline(self, rid):
        return self._paged(f"/client/requests/{rid}/timeline", "timeline", "total_count",
                           key="timeline_id")

    def documents(self, rid):
        return self._paged("/client/request_documents", "documents", "total_documents_count",
                           key="id", request_id=rid)

    def _paged(self, path, list_key, count_key, key, **params):
        items, seen, first, n = [], set(), None, 1
        while True:
            d = self.get_json(path, page_number=n, **params)
            first = first or d
            new = [x for x in d.get(list_key) or [] if x.get(key) not in seen]
            if not new:
                break
            for x in new:
                seen.add(x.get(key))
            items.extend(new)
            if len(items) >= (d.get(count_key) or 0):
                break
            n += 1
        out = {k: v for k, v in first.items() if k != list_key}
        out[list_key] = items
        return out

    def download(self, doc_id):
        # Not cached by default: files can be large and the redirect URL is presigned.
        return self.get(f"{BASE}/documents/{doc_id}/download", accept="*/*", use_cache=False)


# --- parsing helpers -------------------------------------------------------------
def request_url(rid):
    return f"{BASE}/requests/{rid}"


def document_url(doc_id):
    return f"{BASE}/documents/{doc_id}"


def iso_date(s):
    """'09/30/2026', 'September 24, 2026', 'Uploaded: 09/25/2026', or ISO -> 'YYYY-MM-DD'."""
    if not s:
        return None
    s = str(s).strip()
    s = re.sub(r"^[A-Za-z]+:\s*", "", s)  # "Uploaded: ", "Redacted: "
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", s)
    if m:
        return m.group(1)
    for fmt in ("%m/%d/%Y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f"unrecognized date {s!r}")


def plain_text(s):
    s = re.sub(r"<br\s*/?>|</p>", " ", s or "", flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s).replace("\xa0", " ")
    s = re.sub(r"[-]", "", s)  # private-use glyphs (icon fonts)
    return re.sub(r"\s+", " ", s).strip()


def split_departments(s, known=()):
    """Split the listing's comma-joined department_names. Some department names contain
    commas ("Parks, Recreation and Waterfront"), so pieces are rejoined greedily against
    the portal's department list when it is supplied."""
    parts = [p.strip() for p in (s or "").split(",")]
    known = {k.strip() for k in known}
    out, i = [], 0
    while i < len(parts):
        for j in range(len(parts), i, -1):
            cand = ", ".join(parts[i:j]).strip()
            if cand in known:
                out.append(cand); i = j; break
        else:
            if parts[i]:
                out.append(parts[i])
            i += 1
    return out


def is_public_document(d):
    scan = d.get("document_scan") or {}
    return (str(d.get("visibility", "")).lower() == "public"
            or str(scan.get("visibility", "")).lower() == "public")
