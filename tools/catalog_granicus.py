#!/usr/bin/env python3
"""Catalog the City's meeting recordings on Granicus (berkeley.granicus.com).

The catalog records what the City publishes; it does not archive it. Each entry is one
recording as the City's Granicus listing shows it: clip id, name, date and duration as printed,
and the City's own player, agenda, MP3 and MP4 URLs. No media is downloaded. A recording is
archived only when a specific need calls for it, by the usual large-file procedure.

Writes catalog/granicus/<view>.json (view 5 is the City Council). Entries are keyed by clip id
and never deleted: a clip that disappears from the listing is kept and marked with
`last_listed`, so a removal by the City stays visible.

Usage:
  tools/catalog_granicus.py              # City Council (view 5)
  tools/catalog_granicus.py --view 2     # another body's listing
  tools/catalog_granicus.py --html FILE  # parse a saved listing instead of fetching
"""

import argparse
import datetime as dt
import hashlib
import html as htmlmod
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "catalog" / "granicus"
LISTING = "https://berkeley.granicus.com/ViewPublisher.php?view_id={view}"
VIEWS = {"5": "city-council"}
UA = "berkeley-public-records catalog (metadata only)"


def text(fragment):
    return " ".join(htmlmod.unescape(re.sub(r"<[^>]+>", " ", fragment)).replace("\xa0", " ").split())


def cell(row, header):
    m = re.search(r'<td[^>]*headers="%s[^"]*"[^>]*>(.*?)</td>' % re.escape(header), row, re.S)
    return text(m.group(1)) if m else None


def href(row, pattern):
    m = re.search(r'(?:href="|window\.open\(\')((?:https?:)?//[^"\']*%s[^"\']*)' % pattern, row)
    if not m:
        return None
    url = htmlmod.unescape(m.group(1))
    return "https:" + url if url.startswith("//") else url


def parse(listing_html):
    """One dict per recording row, in listing order."""
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", listing_html, re.S):
        clip = re.search(r"clip_id=(\d+)", row)
        name = cell(row, "Name")
        if not clip or not name:
            continue
        listed = cell(row, "Date")
        entry = {
            "clip_id": clip.group(1),
            "name": name,
            "listed_date": listed,
            "date": None,
            "duration": cell(row, "Duration"),
            "player_url": href(row, r"MediaPlayer\.php"),
            "agenda_url": href(row, r"AgendaViewer\.php"),
            "minutes_url": href(row, r"MinutesViewer\.php"),
            "mp3_url": href(row, r"\.mp3"),
            "mp4_url": href(row, r"\.mp4"),
        }
        m = re.match(r"([A-Z][a-z]{2}) (\d{1,2}), (\d{4})", listed or "")
        if m:
            entry["date"] = dt.datetime.strptime(" ".join(m.groups()), "%b %d %Y").date().isoformat()
        out.append(entry)
    return out


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def merge(existing, parsed, today, source):
    """Existing entries are kept; listed entries are refreshed; vanished ones keep last_listed."""
    by_id = {e["clip_id"]: e for e in existing}
    seen = set()
    for e in parsed:
        seen.add(e["clip_id"])
        old = by_id.get(e["clip_id"], {})
        by_id[e["clip_id"]] = {**e, "first_listed": old.get("first_listed", today), "last_listed": today}
    for cid, e in by_id.items():
        if cid not in seen:
            e.setdefault("last_listed", e.get("first_listed"))
    entries = sorted(by_id.values(), key=lambda e: (e.get("date") or "", int(e["clip_id"])), reverse=True)
    return {"source": source, "retrieved": today, "entries": entries}


def run(view="5", html_file=None, today=None):
    today = today or dt.date.today().isoformat()
    url = LISTING.format(view=view)
    raw = Path(html_file).read_bytes() if html_file else fetch(url)
    parsed = parse(raw.decode("utf-8", "replace"))
    if not parsed:
        print(f"no recordings parsed from {url}; nothing written", file=sys.stderr)
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{VIEWS.get(view, 'view-' + view)}.json"
    existing = json.loads(path.read_text())["entries"] if path.exists() else []
    source = {"url": url, "listing_sha256": hashlib.sha256(raw).hexdigest(), "view_id": view}
    data = merge(existing, parsed, today, source)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    new = len({e["clip_id"] for e in parsed} - {e["clip_id"] for e in existing})
    gone = sum(1 for e in data["entries"] if e["last_listed"] != today)
    print(f"{path.relative_to(ROOT)}: {len(data['entries'])} recordings; {new} new; {gone} no longer listed")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--view", default="5")
    ap.add_argument("--html", help="parse a saved listing file instead of fetching")
    a = ap.parse_args()
    sys.exit(run(a.view, a.html))
