#!/usr/bin/env python3
"""Validate the archive: manifest completeness, fingerprints, provenance, and L3 rules.

Standard library only, so any contributor can run it:  python3 tools/validate.py
Exit status 1 on any error. See README.md for the rules enforced here.
"""
import hashlib, json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import source_hosts  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAYERS = ("L0", "T0", "L1", "L2", "L3")
ORIGINALS = ("L0", "T0")   # documents as published: public bodies (L0), third parties (T0)
MAX_IN_GIT = 25 * 1024 * 1024
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
LEVELS = {"verified", "cued", "inferred"}
L3_NOTICE = ("Speaker attribution in this layer is inferred, not authoritative. It was produced by "
             "software and volunteers, not by the City. Verify any attribution against the meeting "
             "video before relying on it.")
REQUIRED = {"id", "layer", "doc_type", "title", "file", "sha256", "bytes", "archived", "contributed_by"}
QUEUE_STATUSES = {"candidate", "watching", "inducted", "rejected"}
DECISION_STATUSES = {"watching", "inducted", "rejected"}
QUEUE_REQUIRED = {"id", "request_date", "state", "visibility", "published", "departments",
                  "request_text_excerpt", "document_count", "url", "first_seen", "last_checked",
                  "status", "rank_hints", "decision"}
REQUEST_ID_RE = re.compile(r"^\d{2}-\d+$")
# Queue decisions are made only by the maintainer (README, "Public-records requests").
MAINTAINERS = {"ericdf"}
# External blob copies (README, "Large files"). A staged copy is the contributor's own, reachable
# by anyone with its link, until the daily archive job makes the archive's copies.
ARCHIVE_STORES = ("internet_archive", "google_drive")
BLOB_STORES = ARCHIVE_STORES + ("staged",)
DRIVE_LINK_RE = re.compile(r"^https://drive\.google\.com/(file/d/[A-Za-z0-9_-]{10,}|open\?id=[A-Za-z0-9_-]{10,})")
LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
# L0 with no public source archived from this date names who approved its Internet Archive copy
# (README, "Records with no public source"). Earlier entries predate the rule.
APPROVAL_FROM = "2026-10-30"
errors = []

def err(where, msg): errors.append(f"{where}: {msg}")

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()

def load_speakers():
    p = os.path.join(ROOT, "registry", "speakers.json")
    reg = json.load(open(p))
    return {s["id"] for s in reg["officeholders"]} | {s["id"] for s in reg["staff_roles"]}

def blob_errors(e):
    """Problems with an external blob's copies (file null). It needs a staged copy, or an archive
    copy whose sha256 has been checked. The Google Drive copy is checked when it is made; the
    Internet Archive copy can be checked only once the item is public, so it may wait."""
    copies = (e.get("blob") or {}).get("copies") or []
    out = copy_errors(copies, BLOB_STORES)
    if not copies:
        out.append("external blob needs at least one copy: a staged copy, or the archive's copies")
    elif not any(c.get("store") == "staged" or (c.get("store") in ARCHIVE_STORES and DATE_RE.match(str(c.get("verified") or "")))
                 for c in copies if isinstance(c, dict)):
        out.append("external blob needs a staged copy or a verified archive copy")
    if any(isinstance(c, dict) and c.get("store") == "staged" for c in copies) and e["layer"] != "L0":
        out.append("only L0 documents are staged; T0 is never uploaded to the archive's stores")
    return out


def in_git_copy_errors(e):
    """A file kept in git may also carry an Internet Archive copy: L0 with no public source does
    (README, "Records with no public source")."""
    copies = (e.get("blob") or {}).get("copies") or []
    out = copy_errors(copies, ("internet_archive",))
    if e["layer"] != "L0":
        out.append("only L0 files kept in git carry an Internet Archive copy")
    if not copies:
        out.append("blob needs copies, or remove it")
    return out


def copy_errors(copies, stores):
    out, seen = [], set()
    if not isinstance(copies, list):
        return ["blob.copies must be a list"]
    for c in copies:
        if not isinstance(c, dict) or c.get("store") not in stores:
            out.append(f"blob copy store must be one of {list(stores)}"); continue
        s = c["store"]
        if s in seen:
            out.append(f"more than one {s} copy")
        seen.add(s)
        if c.get("verified") is not None and not DATE_RE.match(str(c["verified"])):
            out.append(f"{s} copy: verified must be YYYY-MM-DD")
        if s == "google_drive" and not DATE_RE.match(str(c.get("verified") or "")):
            out.append("google_drive copy is not verified; it is checked by download when it is made")
        if s == "staged":
            if not DRIVE_LINK_RE.match(str(c.get("url") or "")):
                out.append("staged copy needs url, a Google Drive file link shared as \"anyone with the link can view\"")
            if not LOGIN_RE.match(str(c.get("staged_by") or "")):
                out.append("staged copy needs staged_by, the GitHub username of who staged it")
            if not DATE_RE.match(str(c.get("staged_on") or "")):
                out.append("staged copy needs staged_on (YYYY-MM-DD)")
    return out


def pending_preservation(e):
    """True while the daily archive job still has work on this entry: a staged copy to replace,
    or an Internet Archive copy not yet checked."""
    copies = (e.get("blob") or {}).get("copies") or []
    return any(isinstance(c, dict) and (c.get("store") == "staged" or
               (c.get("store") == "internet_archive" and not c.get("verified"))) for c in copies)


def approval_errors(e):
    """L0 with no public source is published to the Internet Archive on someone's approval
    (README, "Records with no public source"); approved_by names them."""
    out = []
    a = e.get("approved_by")
    if a is not None and not LOGIN_RE.match(str(a)):
        out.append("approved_by must be a GitHub username")
    if (e["layer"] == "L0" and not e.get("source_url") and str(e.get("archived") or "") >= APPROVAL_FROM
            and a is None and not e.get("withdrawn")):
        out.append("L0 with no source_url needs approved_by: the GitHub username of the contributor who "
                   "approved publishing it to the Internet Archive")
    if a is not None and (e["layer"] != "L0" or e.get("source_url")):
        out.append("approved_by is only for L0 with no source_url")
    return out


def main():
    manifest, files_listed, pending = {}, {}, []
    mdir = os.path.join(ROOT, "manifest")
    for name in sorted(os.listdir(mdir)):
        if not name.endswith(".json"):
            continue
        where = f"manifest/{name}"
        try:
            e = json.load(open(os.path.join(mdir, name)))
        except json.JSONDecodeError as x:
            err(where, f"invalid JSON: {x}"); continue
        missing = REQUIRED - e.keys()
        if missing:
            err(where, f"missing fields {sorted(missing)}"); continue
        if e["id"] + ".json" != name:
            err(where, f"id {e['id']!r} does not match filename")
        if not ID_RE.match(e["id"]):
            err(where, "id must be lowercase letters, digits and hyphens")
        if e["layer"] not in LAYERS:
            err(where, f"layer must be one of {LAYERS}")
        for f in ("meeting_date", "retrieved", "archived"):
            if e.get(f) is not None and not DATE_RE.match(str(e[f])):
                err(where, f"{f} must be YYYY-MM-DD")
        if e.get("document_date") == "undated" and (e["layer"] != "T0" or not (e.get("wayback_url") or e.get("retrieved"))):
            err(where, 'document_date "undated" is for T0 only, with wayback_url or retrieved')
        if e.get("wayback_url") and not re.match(r"^https://web\.archive\.org/web/\d{14}/https?://\S+$", e["wayback_url"]):
            err(where, "wayback_url must be https://web.archive.org/web/<14-digit timestamp>/<address>")
        if e["layer"] == "T0" and not (e.get("publisher") and e.get("relevance")):
            err(where, "T0 needs publisher (who published it) and relevance (the public decision it bears on)")
        if e["layer"] in ORIGINALS and not e.get("source_url") and not e.get("source_note"):
            err(where, "L0 needs source_url or source_note")
        for m in approval_errors(e):
            err(where, m)
        if e["layer"] in ("L1", "L2", "L3"):
            if "derived_from" not in e:
                err(where, f"{e['layer']} needs derived_from (null only with a source_note)")
            elif e["derived_from"] is None and not e.get("source_note"):
                err(where, "derived_from is null without a source_note explaining why")
        if e["layer"] == "L1" and e.get("derived_from") and not (e.get("extraction") or {}).get("command"):
            err(where, "L1 needs extraction.command")
        if e["file"] is None and e.get("withdrawn") is not None:
            w = e["withdrawn"]
            if not isinstance(w, dict) or not DATE_RE.match(str(w.get("date") or "")) or not w.get("reason"):
                err(where, "withdrawn needs date (YYYY-MM-DD) and reason")
            if e["layer"] == "T0" and not (e.get("source_url") or e.get("wayback_url")):
                err(where, "a withdrawn T0 document needs source_url or wayback_url, where readers find it now")
            if e["layer"] not in ("T0", "L1"):
                err(where, "only T0 documents and their L1 text may be withdrawn")
        elif e.get("withdrawn") is not None:
            err(where, "withdrawn needs file null; the bytes are no longer held")
        elif e["file"] is None:
            for m in blob_errors(e):
                err(where, m)
            if e["layer"] not in ORIGINALS:
                err(where, "only L0/T0 files may be external blobs")
            if pending_preservation(e):
                pending.append(e["id"])
        else:
            if e.get("blob") is not None:
                for m in in_git_copy_errors(e):
                    err(where, m)
                if pending_preservation(e):
                    pending.append(e["id"])
            path = os.path.join(ROOT, e["file"])
            if e["file"] in files_listed:
                err(where, f"file also listed by {files_listed[e['file']]}")
            files_listed[e["file"]] = e["id"]
            if not os.path.isfile(path):
                err(where, f"file {e['file']} does not exist")
            else:
                size = os.path.getsize(path)
                if size != e["bytes"]:
                    err(where, f"bytes {e['bytes']} != actual {size}")
                if sha256(path) != e["sha256"]:
                    err(where, "sha256 does not match the file")
                if size > MAX_IN_GIT and e["layer"] in ORIGINALS:
                    err(where, f"{e['layer']} file over 25 MB must be an external blob")
        manifest[e["id"]] = e

    # derived_from must point at a real entry with a matching fingerprint
    # (an L2 dataset built from several documents lists them all)
    for i, e in manifest.items():
        ds = e.get("derived_from")
        for d in (ds if isinstance(ds, list) else [ds] if ds else []):
            src = manifest.get(d.get("id"))
            if not src:
                err(f"manifest/{i}.json", f"derived_from {d.get('id')!r} not in manifest")
            elif src["sha256"] != d.get("sha256"):
                err(f"manifest/{i}.json", "derived_from sha256 does not match the source entry")
        if e["layer"] == "L2" and ds and not (e.get("extraction") or {}).get("command"):
            err(f"manifest/{i}.json", "L2 needs extraction.command, the recorded build")

    # versions: revision_of on the later document, revised_by on the earlier, pointing at each other
    for i, e in manifest.items():
        where = f"manifest/{i}.json"
        r = e.get("revision_of")
        if r is not None:
            src = manifest.get(r)
            if not src:
                err(where, f"revision_of {r!r} is not in the manifest")
            elif i not in (src.get("revised_by") or []):
                err(where, f"revision_of {r!r}, but {r} does not list {i} in revised_by")
            elif src.get("meeting_date") != e.get("meeting_date"):
                err(where, f"revision_of {r!r} belongs to a different meeting date")
        rb = e.get("revised_by")
        if rb is not None:
            if not isinstance(rb, list) or not rb:
                err(where, "revised_by must be a non-empty list of archive ids")
            else:
                for k in rb:
                    if (manifest.get(k) or {}).get("revision_of") != i:
                        err(where, f"revised_by lists {k!r}, whose revision_of is not {i}")
        if e.get("version_label") and not isinstance(e["version_label"], str):
            err(where, "version_label must be text")
        for pa in e.get("published_at") or []:
            if not (isinstance(pa, dict) and re.match(r"^https?://\S+$", str(pa.get("url"))) and DATE_RE.match(str(pa.get("seen")))):
                err(where, "published_at entries need url (https://...) and seen (YYYY-MM-DD)")

    # every file in a layer directory is listed
    for layer in LAYERS:
        for dirpath, _, names in os.walk(os.path.join(ROOT, layer)):
            for n in names:
                rel = os.path.relpath(os.path.join(dirpath, n), ROOT)
                if n == "README.md" or n.startswith("."):
                    continue
                if rel not in files_listed:
                    err(rel, "file is not listed in manifest/")

    # L3 content rules
    speakers = load_speakers()
    for i, e in manifest.items():
        if e["layer"] != "L3" or not e.get("file"):
            continue
        where = e["file"]
        try:
            doc = json.load(open(os.path.join(ROOT, e["file"])))
        except (OSError, json.JSONDecodeError) as x:
            err(where, f"unreadable L3 file: {x}"); continue
        if doc.get("notice") != L3_NOTICE:
            err(where, "L3 file must carry the standard notice verbatim")
        src = manifest.get((e.get("derived_from") or {}).get("id"))
        if not src or src["layer"] != "L1":
            err(where, "L3 must derive from an L1 transcript")
        elif doc.get("l1_sha256") != src["sha256"]:
            err(where, "l1_sha256 does not match the current L1 text; re-anchor the attribution")
        for k, s in enumerate(doc.get("segments", [])):
            at = f"{where} segment {k}"
            lvl = s.get("attribution_level")
            if lvl not in LEVELS:
                err(at, f"attribution_level must be one of {sorted(LEVELS)}")
            if not s.get("basis"):
                err(at, "basis is required")
            sp = s.get("speaker", "")
            if not (sp in speakers or sp.startswith("public:") or sp == "unknown"):
                err(at, f"speaker {sp!r} is not in registry/speakers.json and not public:<name>")
            if lvl == "verified":
                for f in ("video_timestamp", "verified_by", "verified_on"):
                    if not s.get(f):
                        err(at, f"verified segment needs {f}")

    nq = validate_queue()
    validate_inbox()
    for m in source_hosts.problems(ROOT):
        err(source_hosts.PATH, m)

    for e in errors:
        print("ERROR", e)
    n = len(manifest)
    if pending:
        print(f"{len(pending)} entries awaiting the daily archive job: {', '.join(sorted(pending))}")
    print(f"{n} manifest entries; {nq} queue entries; {len(errors)} errors")
    sys.exit(1 if errors else 0)

def validate_inbox():
    """inbox/ holds contributions waiting for tools/process_inbox.py. Nothing may be merged
    while any remain, so only inbox/README.md and inbox/.gitkeep are allowed here."""
    idir = os.path.join(ROOT, "inbox")
    if not os.path.isdir(idir):
        return
    left = sorted(n for n in os.listdir(idir) if n not in ("README.md", ".gitkeep", ".DS_Store"))
    for n in left:
        err(f"inbox/{n}", "unprocessed contribution. Run python3 tools/process_inbox.py (on a pull "
            "request from this repository the inbox workflow runs it automatically and comments "
            "with the result)")

def validate_queue():
    """queue/nextrequest/<id>.json: public-records requests awaiting or holding a decision."""
    qdir = os.path.join(ROOT, "queue", "nextrequest")
    if not os.path.isdir(qdir):
        return 0
    n = 0
    for name in sorted(os.listdir(qdir)):
        where = f"queue/nextrequest/{name}"
        if not name.endswith(".json"):
            if not name.startswith("."):
                err(where, "only <id>.json files belong in the queue")
            continue
        n += 1
        try:
            q = json.load(open(os.path.join(qdir, name)))
        except json.JSONDecodeError as x:
            err(where, f"invalid JSON: {x}"); continue
        missing = QUEUE_REQUIRED - q.keys()
        if missing:
            err(where, f"missing fields {sorted(missing)}"); continue
        if q["id"] + ".json" != name:
            err(where, f"id {q['id']!r} does not match filename")
        if not REQUEST_ID_RE.match(str(q["id"])):
            err(where, "id must look like 26-2872")
        for f in ("request_date", "first_seen", "last_checked"):
            if not DATE_RE.match(str(q[f])):
                err(where, f"{f} must be YYYY-MM-DD")
        if q["status"] not in QUEUE_STATUSES:
            err(where, f"status must be one of {sorted(QUEUE_STATUSES)}")
        if q["document_count"] is not None and not isinstance(q["document_count"], int):
            err(where, "document_count must be an integer or null")
        if not isinstance(q["departments"], list):
            err(where, "departments must be a list")
        if not str(q["url"]).startswith("https://cityofberkeleyca.nextrequest.com/requests/"):
            err(where, "url must be the request's public NextRequest URL")
        d = q["decision"]
        if d is None:
            if q["status"] in ("inducted", "rejected"):
                err(where, f"status {q['status']!r} requires a decision")
            continue
        if not isinstance(d, dict):
            err(where, "decision must be null or an object"); continue
        if d.get("status") not in DECISION_STATUSES:
            err(where, f"decision.status must be one of {sorted(DECISION_STATUSES)}")
        for f in ("reason", "decided_by", "decided_on"):
            if not d.get(f):
                err(where, f"a decided entry needs decision.{f}")
        if d.get("decided_on") and not DATE_RE.match(str(d["decided_on"])):
            err(where, "decision.decided_on must be YYYY-MM-DD")
        if d.get("decided_by") and d["decided_by"] not in MAINTAINERS:
            err(where, f"decision.decided_by {d['decided_by']!r} is not a maintainer")
        if d.get("status") in ("inducted", "rejected") and q["status"] != d["status"]:
            err(where, f"status {q['status']!r} disagrees with decision {d['status']!r}")
        if d.get("status") == "watching" and q["status"] not in ("watching", "candidate"):
            err(where, "a request decided 'watching' has status watching or candidate (re-surfaced)")
    return n


if __name__ == "__main__":
    main()
