#!/usr/bin/env python3
"""Catalog the City Council meetings listed on the City's agenda page.

The City's agenda listing is the system of record for which documents belong to which Council
meeting: https://berkeleyca.gov/your-government/city-council/city-council-agendas . Each row is
one meeting, with the City's links to its HTML agenda, agenda packet (for a special meeting, the
special agenda), annotated agenda, video stream, MP3 and MP4. This tool records those rows. It
downloads no documents, and it records nothing about what the archive holds; that join is made
on demand by tools/meeting_map.py.

Writes catalog/city-council-agendas/meetings.json. Entries are keyed by the City's meeting slug
(the last part of the meeting page's address, e.g. city-council-2026-10-27; the City appends -0,
-1 for further meetings on a date) and never deleted: a meeting the City stops listing keeps its
`last_listed` date, so a removal stays visible.

Usage:
  tools/catalog_city_agendas.py              # fetch the listing and update the catalog
  tools/catalog_city_agendas.py --html FILE  # parse a saved listing instead of fetching
"""

import argparse
import datetime as dt
import hashlib
import html as htmlmod
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "catalog" / "city-council-agendas" / "meetings.json"
SITE = "https://berkeleyca.gov"
LISTING = SITE + "/your-government/city-council/city-council-agendas"
UA = "berkeley-public-records catalog (metadata only)"
# The City's cell classes. The packet column is labeled "Agenda Packet" but classed "minutes".
CELLS = {"name": "council-meeting-name", "date": "council-meeting-date",
         "agenda": "council-meeting-agenda", "packet": "council-meeting-minutes",
         "annotated": "council-meeting-annotated", "video": "council-meeting-video",
         "audio": "council-meeting-audio"}
NAME_RE = re.compile(r"^City Council (\d{4}-\d{2}-\d{2}) - (.+)$")


def text(fragment):
    return " ".join(htmlmod.unescape(re.sub(r"<[^>]+>", " ", fragment)).replace("\xa0", " ").split())


def absolute(url):
    url = htmlmod.unescape(url)
    return urllib.parse.urljoin(SITE + "/", url) if url else None


def cells(row):
    out = {}
    for key, cls in CELLS.items():
        m = re.search(r'<td[^>]*class="%s\b[^"]*"[^>]*>(.*?)</td>' % re.escape(cls), row, re.S)
        out[key] = m.group(1) if m else ""
    return out


def links(fragment):
    return [absolute(h) for h in re.findall(r'href="([^"]+)"', fragment)]


def first(urls, pattern):
    return next((u for u in urls if re.search(pattern, u, re.I)), None)


def parse(listing_html):
    """One dict per meeting row, in listing order."""
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", listing_html, re.S):
        c = cells(row)
        name = text(c["name"])
        m = NAME_RE.match(name)
        page = first(links(c["name"]), r"/city-council-agendas/")
        if not m or not page:
            continue
        when = re.search(r'<time datetime="([^"]+)"', c["date"])
        audio = links(c["audio"])
        out.append({
            "meeting": page.rstrip("/").rsplit("/", 1)[1],
            "name": name,
            "date": m.group(1),
            "label": m.group(2).strip().lower(),
            "listed_time": text(c["date"]) or None,
            "start_utc": when.group(1) if when else None,
            "page_url": page,
            "city": {
                "agenda_html": first(links(c["agenda"]), r"."),
                "agenda_packet": first(links(c["packet"]), r"\.pdf($|\?)"),
                "annotated_agenda": first(links(c["annotated"]), r"\.pdf($|\?)"),
                "video": first(links(c["video"]), r"."),
                "mp3": first(audio, r"\.mp3($|\?)"),
                "mp4": first(audio, r"\.mp4($|\?)"),
            },
        })
    return out


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def merge(existing, parsed, today, source):
    """Existing entries are kept; listed entries are refreshed; vanished ones keep last_listed."""
    by_key = {e["meeting"]: e for e in existing}
    seen = set()
    for e in parsed:
        seen.add(e["meeting"])
        old = by_key.get(e["meeting"], {})
        by_key[e["meeting"]] = {**e, "first_listed": old.get("first_listed", today), "last_listed": today}
    for key, e in by_key.items():
        if key not in seen:
            e.setdefault("last_listed", e.get("first_listed"))
    entries = sorted(by_key.values(), key=lambda e: (e["date"], e.get("start_utc") or "", e["meeting"]),
                     reverse=True)
    return {"source": source, "retrieved": today, "entries": entries}


def load(path=OUT):
    return json.loads(Path(path).read_text())["entries"] if Path(path).exists() else []


def run(html_file=None, today=None, out=OUT):
    today = today or dt.date.today().isoformat()
    raw = Path(html_file).read_bytes() if html_file else fetch(LISTING)
    parsed = parse(raw.decode("utf-8", "replace"))
    if not parsed:
        print(f"no meetings parsed from {LISTING}; nothing written", file=sys.stderr)
        return 1
    dupes = sorted({e["meeting"] for e in parsed if sum(x["meeting"] == e["meeting"] for x in parsed) > 1})
    if dupes:
        print(f"the listing repeats meeting slugs {dupes}; nothing written", file=sys.stderr)
        return 1
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    existing = load(out)
    source = {"url": LISTING, "listing_sha256": hashlib.sha256(raw).hexdigest()}
    data = merge(existing, parsed, today, source)
    out.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    new = len({e["meeting"] for e in parsed} - {e["meeting"] for e in existing})
    gone = sum(1 for e in data["entries"] if e["last_listed"] != today)
    print(f"{out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}: {len(data['entries'])} meetings; "
          f"{new} new; {gone} no longer listed")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--html", help="parse a saved listing file instead of fetching")
    a = ap.parse_args()
    sys.exit(run(a.html))
