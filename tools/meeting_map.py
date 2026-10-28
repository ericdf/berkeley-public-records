#!/usr/bin/env python3
"""Join each Council meeting the City lists to the archive's documents for it.

Reads three things and writes nothing to the repository:
  catalog/city-council-agendas/meetings.json   what the City lists, per meeting
  manifest/                                    what the archive holds
  catalog/granicus/city-council.json           the City's recordings

and reports, for every meeting, which archive ids fill each role: agenda packet, annotated
agenda, caption transcripts, agenda item material, packet item index, and the Granicus clip of
its recording. The join is computed on demand rather than stored, so it can never fall out of
date with the manifest.

Matching, in order of preference:
  - a manifest entry whose source_url is the City's link for that meeting;
  - otherwise meeting_date plus meeting_label, compared case-insensitively word by word, so a
    caption transcript labeled "Special and Regular" belongs to both meetings of that date;
  - a Council document with no label belongs to the date's meeting only when there is exactly
    one non-closed, non-canceled meeting that day.
Recordings join by the MP3 or MP4 URL, which the City page and the Granicus listing share.

Usage:
  tools/meeting_map.py                    # gap report: what the City lists that is not archived
  tools/meeting_map.py --since 2026-01-01 # gap report from a date (default: the earliest
                                          # archived caption transcript or annotated agenda)
  tools/meeting_map.py --meeting city-council-2026-10-27
  tools/meeting_map.py --json FILE        # the full map as JSON ("-" for stdout)
"""
import argparse, json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COUNCIL = "Berkeley City Council"
NO_DOCUMENTS = {"closed session", "canceled"}
ROLES = {  # role -> manifest doc_type, and the City link that identifies it
    "agenda_packet": ("agenda_packet", "agenda_packet"),
    "annotated_agenda": ("annotated_agenda", "annotated_agenda"),
    "captions": ("council_captions", None),
    "items": (None, None),
    "packet_index": ("agenda_packet_items", None),
}


def words(label):
    return set(re.findall(r"[a-z]+", (label or "").lower()))


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


def load_manifest(root):
    out, d = {}, os.path.join(root, "manifest")
    for n in sorted(os.listdir(d)):
        if n.endswith(".json"):
            e = load_json(os.path.join(d, n), None)
            out[e["id"]] = e
    return out


def role_of(e):
    if e.get("body", COUNCIL) != COUNCIL and e.get("doc_type") != "council_captions":
        return None
    for role, (doc_type, _) in ROLES.items():
        if doc_type and e.get("doc_type") == doc_type and e["layer"] in ("L0", "L2"):
            return role
    if e["layer"] == "L0" and e.get("collection") in ("staff_reports", "staff-reports") and e.get("body") == COUNCIL:
        return "items"
    return None


def build(root=ROOT):
    meetings = load_json(os.path.join(root, "catalog", "city-council-agendas", "meetings.json"),
                         {"entries": []})["entries"]
    clips = load_json(os.path.join(root, "catalog", "granicus", "city-council.json"), {"entries": []})["entries"]
    manifest = load_manifest(root)
    by_date = {}
    for m in meetings:
        by_date.setdefault(m["date"], []).append(m)
    out = {m["meeting"]: {"meeting": m["meeting"], "date": m["date"], "label": m["label"],
                          "city": m["city"], "archived": {r: [] for r in ROLES}, "recording_clips": []}
           for m in meetings}
    by_url = {}
    for m in meetings:
        for role, (_, link) in ROLES.items():
            if link and m["city"].get(link):
                by_url[m["city"][link]] = m["meeting"]
    unmatched = []
    for i, e in sorted(manifest.items()):
        role = role_of(e)
        if not role:
            continue
        hits = []
        if e.get("source_url") in by_url:
            hits = [by_url[e["source_url"]]]
        elif e.get("meeting_date"):
            same_day = [m for m in by_date.get(e["meeting_date"], []) if m["label"] not in NO_DOCUMENTS]
            label = words(e.get("meeting_label"))
            if label:
                hits = [m["meeting"] for m in same_day if words(m["label"]) <= label]
            elif len(same_day) == 1:
                hits = [same_day[0]["meeting"]]
        for h in hits:
            out[h]["archived"][role].append(i)
        if not hits and e.get("meeting_date"):
            unmatched.append(i)
    for c in clips:
        for m in meetings:
            if any(c.get(k) and c[k] == m["city"].get(k[:3]) for k in ("mp3_url", "mp4_url")):
                out[m["meeting"]]["recording_clips"].append(c["clip_id"])
    return {"meetings": [out[m["meeting"]] for m in meetings], "unmatched": unmatched}


def gaps(data, since=None):
    """Lines describing what the City lists for each meeting that the archive does not hold."""
    ms = data["meetings"]
    if since is None:
        held = [m["date"] for m in ms if m["archived"]["captions"] or m["archived"]["annotated_agenda"]]
        since = min(held) if held else "0000-00-00"
    lines = []
    for m in sorted(ms, key=lambda m: (m["date"], m["meeting"])):
        if m["date"] < since or m["label"] in NO_DOCUMENTS:
            continue
        a, c, need = m["archived"], m["city"], []
        if c.get("annotated_agenda") and not a["annotated_agenda"]:
            need.append("annotated agenda")
        if c.get("agenda_packet") and not a["agenda_packet"]:
            if m["label"] == "regular":
                need.append("agenda packet")
            elif not a["annotated_agenda"]:
                need.append("special agenda (no annotated agenda archived)")
            else:
                need.append("special agenda (archive only if it carries item material)")
        if c.get("mp3") and not a["captions"]:
            need.append("caption transcript (from the City Clerk)")
        if a["agenda_packet"] and not a["packet_index"]:
            need.append("packet item index")
        if need:
            lines.append(f"{m['date']}  {m['label']:<8}  {m['meeting']:<28}  " + "; ".join(need))
    return since, lines


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", help="gap report from this date, YYYY-MM-DD")
    ap.add_argument("--meeting", help="show one meeting's record")
    ap.add_argument("--json", help="write the full map as JSON to FILE, or - for stdout")
    ap.add_argument("--root", default=ROOT)
    a = ap.parse_args(argv)
    data = build(a.root)
    if not data["meetings"]:
        print("catalog/city-council-agendas/meetings.json is missing or empty; run "
              "tools/catalog_city_agendas.py first", file=sys.stderr)
        return 1
    if a.json:
        s = json.dumps(data, indent=1, ensure_ascii=False) + "\n"
        if a.json == "-":
            sys.stdout.write(s)
        else:
            with open(a.json, "w") as f:
                f.write(s)
        return 0
    if a.meeting:
        m = next((m for m in data["meetings"] if m["meeting"] == a.meeting), None)
        if not m:
            print(f"no meeting {a.meeting!r} in the City catalog", file=sys.stderr)
            return 1
        print(json.dumps(m, indent=1, ensure_ascii=False))
        return 0
    since, lines = gaps(data, a.since)
    print(f"Listed by the City since {since} and not archived ({len(lines)} meetings):")
    print("\n".join(lines) if lines else "  nothing")
    if data["unmatched"]:
        print(f"\n{len(data['unmatched'])} archived meeting documents match no listed meeting "
              "(the City listing begins in 2021; committee and pre-2021 material is expected here):")
        for i in data["unmatched"]:
            print("  " + i)
    return 0


if __name__ == "__main__":
    sys.exit(main())
