#!/usr/bin/env python3
"""Daily intake: annotated agendas from the City's site, and caption transcripts the City Clerk
emails, into inbox/ for tools/process_inbox.py. README, "The daily intake job", is the policy.

Annotated agendas. Each Council meeting the City lists (catalog/city-council-agendas, refreshed
first) is checked on a schedule set by its age (`due`): daily for 21 days after the meeting,
weekly (Mondays) to day 60, then only in the quarterly sweep. A check is one ranged GET of one
byte (the City refuses HEAD) for the Last-Modified time. A document is downloaded when the
archive has none for the meeting, when the City's page now links a different file than the one
archived (the City relinks to a new upload), or when the archived URL was modified after it was
retrieved. A download whose bytes the archive already holds is dropped; otherwise it is filed as
a new version (`revision_of`) of the latest version held.

Caption transcripts. The Clerk-mail script (ops/clerk-mail in the ops repository) saves each
Clerk reply's attachments to a Drive folder with a sidecar `<file>.json` recording the sender, subject and
the date received; the workflow copies that folder to --drive-dir. A transcript is filed only
with that sidecar, since the date received is never guessed. Files whose bytes the archive
already holds are listed for removal from the folder without being filed again.

Writes inbox/<id>.<ext> and inbox/<id>.json, and a plan (--plan FILE) listing what was staged
and which Drive files may be removed once the commit is on main. Changes nothing else.

  tools/intake_daily.py --plan plan.json [--drive-dir DIR] [--today YYYY-MM-DD] [--sweep] [--dry-run]
"""
import argparse, datetime as dt, email.utils, hashlib, json, os, re, subprocess, sys, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG = os.path.join(ROOT, "catalog", "city-council-agendas", "meetings.json")
UA = "berkeley-public-records daily intake"
APPROVER = "ericdf"   # standing approval for Clerk-emailed captions; README, "The daily intake job"
COMMITTEES = {"budget": ("Berkeley City Council Budget & Finance Policy Committee", "bcc-budget-finance-committee")}
CLERK_SENDERS = re.compile(r"@berkeleyca\.gov$", re.I)


# ---------- schedule ----------
def due(meeting_date, today, sweep=False):
    """Whether a meeting's documents are checked today: daily to day 21, Mondays to day 60,
    and every meeting from the past year in a sweep."""
    age = (today - meeting_date).days
    if age < 0:
        return False
    if age <= 21:
        return True
    if age <= 60:
        return today.weekday() == 0
    return sweep and age <= 366


def quarterly(today):
    return today.day == 1 and today.month in (1, 4, 7, 10)


# ---------- archive state ----------
def load_manifest(root=ROOT):
    out = {}
    d = os.path.join(root, "manifest")
    for n in os.listdir(d):
        if n.endswith(".json"):
            with open(os.path.join(d, n), encoding="utf-8") as f:
                e = json.load(f)
            out[e["id"]] = e
    return out


def annotated_versions(manifest, date, label):
    """Annotated-agenda entries for one meeting, oldest first."""
    v = [e for e in manifest.values() if e.get("doc_type") == "annotated_agenda"
         and e.get("meeting_date") == date and (e.get("meeting_label") or "").lower() == label.lower()]
    return sorted(v, key=lambda e: (e.get("archived") or "", e["id"]))


def next_id(base, manifest):
    if base not in manifest:
        return base
    n = 2
    while f"{base}-v{n}" in manifest:
        n += 1
    return f"{base}-v{n}"


# ---------- network ----------
def last_modified(url, run=subprocess.run):
    """Last-Modified of url by a one-byte ranged GET, as an aware datetime, or None."""
    r = run(["curl", "-sS", "-m", "30", "-A", UA, "-o", os.devnull, "-D", "-", "-r", "0-0", url],
            capture_output=True, text=True)
    lm = [l.split(":", 1)[1].strip() for l in r.stdout.splitlines() if l.lower().startswith("last-modified:")]
    return email.utils.parsedate_to_datetime(lm[-1]) if lm else None


def download(url, dest, run=subprocess.run):
    r = run(["curl", "-sS", "-f", "-m", "120", "-A", UA, "-o", dest, url], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"download failed: {url}: {r.stderr.strip()}")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


# ---------- annotated agendas ----------
def plan_annotated(entries, manifest, today, sweep, lm_of):
    """Decide which annotated agendas to fetch. Returns [(entry, url, reason, earlier_id)]."""
    out = []
    for m in entries:
        if m.get("label") not in ("regular", "special"):
            continue
        url = (m.get("city") or {}).get("annotated_agenda")
        if not url or not due(dt.date.fromisoformat(m["date"]), today, sweep):
            continue
        held = annotated_versions(manifest, m["date"], m["label"])
        if not held:
            out.append((m, url, "first posting", None)); continue
        latest = held[-1]
        if url != latest.get("source_url") and url not in {e.get("source_url") for e in held}:
            out.append((m, url, "the City's page now links a different file", latest["id"])); continue
        lm = lm_of(url)
        got = latest.get("retrieved") or latest.get("archived")
        if lm and got and lm.date() > dt.date.fromisoformat(got):
            out.append((m, url, f"modified {lm.date().isoformat()}, after it was retrieved {got}", latest["id"]))
    return out


def annotated_meta(m, url, today, earlier_id, reason):
    label = m["label"]
    meta = {"layer": "L0", "collection": "annotated-agendas", "body": "Berkeley City Council",
            "meeting_date": m["date"], "meeting_label": label,
            "publisher": "City of Berkeley — City Clerk Department",
            "title": f"Annotated Agenda — Berkeley City Council {label} meeting, {m['date']}",
            "document_date": m["date"], "source_url": url, "retrieved": today.isoformat()}
    if earlier_id:
        meta["revision_of"] = earlier_id
        meta["version_note"] = f"Found by the daily intake job on {today.isoformat()}: {reason}."
    return meta


# ---------- Clerk captions ----------
CAPTION_NAME = re.compile(r"(\d{4}-\d{2}-\d{2})\s+(.+?)\s+Captioning", re.I)


def caption_meta(fname, side):
    """Inbox metadata for one Clerk transcript and its sidecar, or (None, reason)."""
    m = CAPTION_NAME.search(fname)
    if not m:
        return None, "file name does not give the meeting date and label"
    date, rest = m.group(1), m.group(2).strip()
    received = (side or {}).get("received")
    sender = (side or {}).get("from", "")
    if not received or not re.match(r"\d{4}-\d{2}-\d{2}$", received):
        return None, "no recorded date received"
    if not CLERK_SENDERS.search(re.sub(r".*<|>.*", "", sender)):
        return None, f"not from a City of Berkeley address: {sender!r}"
    ext = os.path.splitext(fname)[1].lower().lstrip(".")
    meta = {"layer": "L0", "collection": "council-captions", "meeting_date": date,
            "document_date": date, "title": os.path.splitext(fname)[0],
            "publisher": "City of Berkeley — City Clerk Department",
            "source_note": f"Caption transcript emailed by the City of Berkeley City Clerk Department "
                           f"({sender}); received {received}.",
            "approved_by": APPROVER, "retrieved": received}
    low = rest.lower()
    committee = next((v for k, v in COMMITTEES.items() if k in low), None)
    if committee:
        meta["body"], prefix = committee[0], committee[1]
        meta["meeting_label"] = "regular"
        stem = f"{prefix}-{date}-regular-captions"
    elif re.search(r"\b(regular|special|jpfa)\b", low):
        meta["body"] = "Berkeley City Council"
        meta["meeting_label"] = " ".join({"jpfa": "JPFA", "and": "and"}.get(w.lower(), w.capitalize()) for w in rest.split())
        stem = f"bcc-{date}-{re.sub(r'[^a-z0-9]+', '-', low).strip('-')}-captions"
    else:
        return None, f"meeting '{rest}' is neither a Council meeting nor a known committee"
    if ext == "pdf":
        meta["notices"] = ["This information provided by Zoom. The City of Berkeley cannot certify the "
                           "following text since we did not create it."]
    return (stem, ext, meta), None


def plan_captions(drive_dir, manifest):
    """[(path, stem, ext, meta)], [(path, reason)] skipped, [path] already archived."""
    stage, skip, held = [], [], []
    if not drive_dir or not os.path.isdir(drive_dir):
        return stage, skip, held
    by_sha = {e.get("sha256") for e in manifest.values()}
    for n in sorted(os.listdir(drive_dir)):
        p = os.path.join(drive_dir, n)
        if n.endswith(".json") or not os.path.isfile(p):
            continue
        if sha256(p) in by_sha:
            held.append(p); continue
        side = None
        if os.path.exists(p + ".json"):
            with open(p + ".json", encoding="utf-8") as f:
                side = json.load(f)
        got, why = caption_meta(n, side)
        if not got:
            skip.append((p, why)); continue
        stem, ext, meta = got
        stage.append((p, stem, ext, meta))
    return stage, skip, held


# ---------- main ----------
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--drive-dir")
    ap.add_argument("--today")
    ap.add_argument("--sweep", action="store_true", help="check every meeting from the past year")
    ap.add_argument("--dry-run", action="store_true", help="report the plan; download and stage nothing")
    a = ap.parse_args(argv)
    today = dt.date.fromisoformat(a.today) if a.today else dt.datetime.now(dt.timezone.utc).date()
    sweep = a.sweep or quarterly(today)
    manifest = load_manifest()
    with open(CATALOG, encoding="utf-8") as f:
        entries = json.load(f)["entries"]
    inbox = os.path.join(ROOT, "inbox")
    plan = {"today": today.isoformat(), "sweep": sweep, "annotated": [], "captions": [], "skipped": [],
            "remove_from_drive": [], "dropped_identical": []}

    checks = []
    def lm_of(url):
        t = last_modified(url); checks.append([url, t.isoformat() if t else None]); return t
    for m, url, reason, earlier in plan_annotated(entries, manifest, today, sweep, lm_of):
        base = f"bcc-{m['date']}-{m['label']}-annotated-agenda"
        nid = next_id(base, manifest)
        rec = {"id": nid, "url": url, "reason": reason, "revision_of": earlier}
        if a.dry_run:
            plan["annotated"].append(rec); continue
        dest = os.path.join(inbox, nid + ".pdf")
        download(url, dest)
        digest = sha256(dest)
        if any(e.get("sha256") == digest for e in manifest.values()):
            os.remove(dest); plan["dropped_identical"].append(rec); continue
        with open(os.path.join(inbox, nid + ".json"), "w", encoding="utf-8") as f:
            json.dump(annotated_meta(m, url, today, earlier, reason), f, indent=2, ensure_ascii=False)
        manifest[nid] = {"id": nid, "sha256": digest}   # reserve the id within this run
        plan["annotated"].append(rec)
    plan["checked"] = checks

    stage, skip, held = plan_captions(a.drive_dir, manifest)
    plan["skipped"] = [{"file": os.path.basename(p), "reason": r} for p, r in skip]
    plan["remove_from_drive"] = [os.path.basename(p) for p in held]
    for p, stem, ext, meta in stage:
        nid = next_id(stem, manifest)
        rec = {"id": nid, "file": os.path.basename(p)}
        if not a.dry_run:
            with open(p, "rb") as src, open(os.path.join(inbox, f"{nid}.{ext}"), "wb") as dst:
                dst.write(src.read())
            with open(os.path.join(inbox, nid + ".json"), "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2, ensure_ascii=False)
            manifest[nid] = {"id": nid}
        plan["captions"].append(rec)
        plan["remove_from_drive"].append(os.path.basename(p))   # removed only after the commit lands

    with open(a.plan, "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2)
    print(json.dumps({k: v for k, v in plan.items() if k != "checked"}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
