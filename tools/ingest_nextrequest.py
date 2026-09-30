#!/usr/bin/env python3
"""Ingest inducted NextRequest public-records requests into L0/cpra/ (and L1 text).

  python3 tools/ingest_nextrequest.py <request id> [...] [--contributor USER] [--root DIR]

Refuses any request whose queue entry (queue/nextrequest/<id>.json) is not decided
"inducted" by the maintainer. For each request it writes:

  L0/cpra/<id>/cpra-<id>-request.json      the request record, full timeline, and
                                           document index as the portal serves them
  L0/cpra/<id>/cpra-<id>-doc-<doc id>.<ext> every PUBLIC released document
  L1/cpra/<id>/cpra-<id>-doc-<doc id>-text.txt   pdftotext -layout, when the PDF has text

with one manifest entry per file. Idempotent: a document already in the manifest is
skipped; a request record is re-snapshotted under a dated id only if its content changed.

Files over 25 MB are NOT written to the repository or the manifest. They are saved to
--blob-staging (outside the repository) and listed in queue/pending_blobs.json with the
manifest entry they will get once two verified external copies exist (README, "Large
files"); uploading them is a separate, approved step.

Standard library only (plus the pdftotext binary for L1).
"""
import argparse, datetime, hashlib, json, os, re, subprocess, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nextrequest_client as nr  # noqa: E402
from nextrequest_watch import ROOT, load_queue  # noqa: E402

MAX_IN_GIT = 25 * 1024 * 1024
DEFAULT_STAGING = os.path.expanduser("~/berkeley-public-records-pending-blobs")


class Refused(Exception):
    pass


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def load_manifest(root):
    d, out = os.path.join(root, "manifest"), {}
    if os.path.isdir(d):
        for n in os.listdir(d):
            if n.endswith(".json"):
                with open(os.path.join(d, n)) as f:
                    e = json.load(f)
                out[e["id"]] = e
    return out


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.write("\n")


def write_bytes(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def pdftotext_version():
    try:
        p = subprocess.run(["pdftotext", "-v"], capture_output=True, text=True)
    except FileNotFoundError:
        return None
    m = re.search(r"pdftotext version \S+", p.stderr + p.stdout)
    return m.group(0) if m else "pdftotext"


def extension(doc):
    ext = (doc.get("file_extension") or "").lower().strip(".")
    if not ext:
        m = re.search(r"\.([A-Za-z0-9]{2,5})$", doc.get("title") or "")
        ext = m.group(1).lower() if m else "bin"
    return ext


def request_record(client, rid):
    """The request, its full timeline, and its document index, as one JSON document.
    Volatile fields (documents_state_timestamp) are dropped so an unchanged request
    produces identical bytes on every run."""
    docs = client.documents(rid)
    docs.pop("documents_state_timestamp", None)
    return {"request": client.request(rid), "timeline": client.timeline(rid), "documents": docs}


def check_inducted(queue, rid):
    e = queue.get(rid)
    if e is None:
        raise Refused(f"{rid}: not in queue/nextrequest/; run nextrequest_watch.py first")
    dec = e.get("decision") or {}
    if dec.get("status") != "inducted":
        raise Refused(f"{rid}: decision is {dec.get('status') or 'not yet made'}; only requests the maintainer "
                      f"has decided \"inducted\" may be ingested")
    return e


def ingest(client, root, rid, contributor, staging=DEFAULT_STAGING, extract=True, log=print):
    queue = load_queue(root)
    qe = check_inducted(queue, rid)
    manifest = load_manifest(root)
    today = datetime.date.today().isoformat()
    added, skipped, pending, notes = [], [], [], []
    rurl = nr.request_url(rid)
    rec = request_record(client, rid)
    req = rec["request"]
    departments = [d["name"] for d in req.get("departments") or []] or qe.get("departments") or []
    publisher = "City of Berkeley" + (" — " + "; ".join(departments) if departments else "")
    base_cpra = {"request_id": rid, "request_date": qe.get("request_date"),
                 "department": departments}

    # --- request record --------------------------------------------------------
    body = (json.dumps(rec, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode()
    digest = sha256_bytes(body)
    rec_id = f"cpra-{rid}-request"
    prior = [m for i, m in manifest.items() if i == rec_id or i.startswith(rec_id + "-")]
    if any(m["sha256"] == digest for m in prior):
        skipped.append(rec_id)
    else:
        if prior:
            rec_id = f"{rec_id}-{today.replace('-', '')}"
        if rec_id in manifest:
            skipped.append(rec_id)
        else:
            rel = f"L0/cpra/{rid}/{rec_id}.json"
            write_bytes(os.path.join(root, rel), body)
            entry = {
                "id": rec_id, "layer": "L0", "doc_type": "cpra_request_record", "collection": "cpra",
                "publisher": publisher,
                "title": f"Public records request {rid}: request, timeline and document index (NextRequest)",
                "document_date": qe.get("request_date"),
                "file": rel, "sha256": digest, "bytes": len(body),
                "source_url": rurl, "retrieved": today, "archived": today, "wayback_url": None,
                "format_note": ("JSON object with keys request, timeline and documents, holding the "
                                "portal's responses from /client/requests/<id>, "
                                "/client/requests/<id>/timeline (all pages) and "
                                "/client/request_documents?request_id=<id> (all pages; "
                                "documents_state_timestamp removed), serialized with sorted keys."),
                "cpra": dict(base_cpra, request_state=req.get("request_state")),
                "notices": [], "contributed_by": contributor,
            }
            write_json(os.path.join(root, "manifest", rec_id + ".json"), entry)
            added.append(rec_id)

    # --- documents -------------------------------------------------------------
    tool = pdftotext_version() if extract else None
    for doc in rec["documents"].get("documents") or []:
        if not nr.is_public_document(doc):
            continue
        did = doc["id"]
        mid = f"cpra-{rid}-doc-{did}"
        if mid in manifest:
            skipped.append(mid); continue
        data = client.download(did)
        digest = sha256_bytes(data)
        ext = extension(doc)
        scan = doc.get("document_scan") or {}
        entry = {
            "id": mid, "layer": "L0", "doc_type": "cpra_document", "collection": "cpra",
            "publisher": publisher,
            "title": doc.get("title") or f"Document {did}",
            "document_date": nr.iso_date(scan.get("upload_date") or doc.get("upload_date")),
            "file": f"L0/cpra/{rid}/{mid}.{ext}", "sha256": digest, "bytes": len(data),
            "source_url": rurl, "retrieved": today, "archived": today, "wayback_url": None,
            "cpra": dict(base_cpra,
                         document_id=did,
                         document_url=nr.document_url(did),
                         upload_date=nr.iso_date(scan.get("upload_date") or doc.get("upload_date")),
                         redacted_date=nr.iso_date(doc.get("redacted_date")),
                         review_state=doc.get("review_state"),
                         folder=" / ".join(x for x in (doc.get("folder_name"), doc.get("subfolder_name")) if x) or None),
            "notices": [], "contributed_by": contributor,
        }
        if len(data) > MAX_IN_GIT:
            os.makedirs(staging, exist_ok=True)
            spath = os.path.join(staging, f"{mid}.{ext}")
            write_bytes(spath, data)
            entry["file"] = None
            entry["blob"] = {"copies": []}
            pending.append({"id": mid, "staged_at": spath, "manifest_entry": entry})
            continue
        write_bytes(os.path.join(root, entry["file"]), data)
        write_json(os.path.join(root, "manifest", mid + ".json"), entry)
        added.append(mid)

        if extract and ext == "pdf":
            if not tool:
                notes.append(f"{mid}: pdftotext not installed; no L1 text"); continue
            with tempfile.TemporaryDirectory() as td:
                out = os.path.join(td, "t.txt")
                p = subprocess.run(["pdftotext", "-layout", os.path.join(root, entry["file"]), out],
                                   capture_output=True)
                text = b""
                if p.returncode == 0 and os.path.exists(out):
                    with open(out, "rb") as f:
                        text = f.read()
            if not text.strip():
                notes.append(f"{mid}: no extractable text (needs OCR with a recorded command)")
                continue
            tid = mid + "-text"
            trel = f"L1/cpra/{rid}/{tid}.txt"
            write_bytes(os.path.join(root, trel), text)
            write_json(os.path.join(root, "manifest", tid + ".json"), {
                "id": tid, "layer": "L1", "doc_type": "cpra_document_text", "collection": "cpra",
                "title": entry["title"], "file": trel,
                "sha256": sha256_bytes(text), "bytes": len(text),
                "derived_from": {"id": mid, "sha256": digest},
                "extraction": {"tool": tool, "command": "pdftotext -layout <L0> <L1>"},
                "notices": [], "contributed_by": contributor, "archived": today,
            })
            added.append(tid)

    if pending:
        ppath = os.path.join(root, "queue", "pending_blobs.json")
        existing = []
        if os.path.exists(ppath):
            with open(ppath) as f:
                existing = json.load(f)
        have = {p["id"] for p in existing}
        existing += [p for p in pending if p["id"] not in have]
        write_json(ppath, existing)
    for n in notes:
        log("note:", n)
    for p in pending:
        log(f"PENDING EXTERNAL BLOB {p['id']} ({p['manifest_entry']['bytes']} bytes) staged at "
            f"{p['staged_at']}; needs two verified copies before it can enter the manifest")
    log(f"{rid}: {len(added)} added, {len(skipped)} already present, {len(pending)} pending blobs")
    return {"added": added, "skipped": skipped, "pending_blobs": pending, "notes": notes}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ids", nargs="+", help="request ids, e.g. 26-2872")
    ap.add_argument("--contributor", default="ericdf", help="GitHub username for contributed_by")
    ap.add_argument("--root", default=ROOT, help="archive root (default: this repository)")
    ap.add_argument("--blob-staging", default=DEFAULT_STAGING,
                    help="where files over 25 MB are saved (must be outside the repository)")
    ap.add_argument("--no-text", action="store_true", help="skip L1 text extraction")
    ap.add_argument("--cache-dir", help="cache portal JSON here (development only)")
    a = ap.parse_args(argv)
    if os.path.abspath(a.blob_staging).startswith(os.path.abspath(a.root) + os.sep):
        ap.error("--blob-staging must be outside the archive")
    queue = load_queue(a.root)
    try:
        for rid in a.ids:
            check_inducted(queue, rid)
    except Refused as x:
        print("REFUSED", x, file=sys.stderr)
        sys.exit(2)
    client = nr.Client(cache_dir=a.cache_dir)
    for rid in a.ids:
        ingest(client, a.root, rid, a.contributor, a.blob_staging, not a.no_text)


if __name__ == "__main__":
    main()
