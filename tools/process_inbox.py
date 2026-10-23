#!/usr/bin/env python3
"""Process contributions dropped into inbox/: file each document, fingerprint it, write its
manifest entry, and extract L1 text.

Each contribution is two files with the same name:

  inbox/<name>.pdf     the document (PDF, or another document type; see DOC_EXTENSIONS)
  inbox/<name>.json    what it is and where it came from (see inbox/README.md)

For every pair this moves the document to L0/<collection>/ or T0/<collection>/, writes
manifest/<id>.json, writes L1 text with `pdftotext -layout` when the PDF has a text layer,
and deletes the two inbox files. Nothing is changed unless every contribution in the inbox
is valid: fix the errors it prints and run it again.

Runs in the inbox GitHub Action, and locally:

  python3 tools/process_inbox.py --contributor <your GitHub username>

Standard library only (plus the pdftotext binary, from poppler, for L1 text).
"""
import argparse, datetime, hashlib, json, os, re, shutil, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAX_IN_GIT = 25 * 1024 * 1024
MIN_WORDS = 30
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DOC_DATE_RE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")
WAYBACK_RE = re.compile(r"^https://web\.archive\.org/web/\d{14}/https?://\S+$")
TOPIC_RE = re.compile(r"^[a-z0-9][a-z0-9_]*$")
T0_COLLECTION_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
IGNORED = {"README.md", ".gitkeep"}
DOC_EXTENSIONS = {"pdf", "doc", "docx", "xls", "xlsx", "csv", "geojson", "ppt", "pptx", "txt", "htm", "html",
                  "rtf", "png", "jpg", "jpeg", "tif", "tiff"}
FIELDS = {
    "layer": "L0 (made or published by a public body) or T0 (published by someone else)",
    "collection": "where it is filed, e.g. sources/budgets, clerk, council-captions, staff-reports, press",
    "title": "the document's title as printed on it",
    "document_date": ("the date on the document: YYYY-MM-DD (or YYYY-MM, or YYYY); optional for staff-reports; "
                      "\"undated\" for a T0 page that prints no date, which then needs wayback_url or retrieved"),
    "publisher": "who made or published it (required for T0)",
    "relevance": "T0 only: the public decision or official it bears on, in neutral terms",
    "source_url": "where it was published online",
    "source_note": "if not online: how and when you obtained it",
    "retrieved": "optional: the date you downloaded it, YYYY-MM-DD",
    "wayback_url": ("optional: the Wayback Machine capture the file was taken from, "
                    "https://web.archive.org/web/<14-digit timestamp>/<address>"),
    "meeting_date": "optional: the meeting it belongs to, YYYY-MM-DD (required for staff-reports)",
    "meeting_label": "council-captions, annotated-agendas, and Council staff-reports: e.g. regular, special",
    "item": "optional: the agenda item as printed, e.g. Consent Calendar Item 12 (required for staff-reports)",
    "body": "staff-reports: the body whose agenda it is on, e.g. Berkeley City Council, Planning Commission",
    "part": "staff-reports, optional: a short name for the document within the item when it is not "
            "the report itself, e.g. lease-list",
    "notices": "optional: disclaimers printed on the document, copied word for word",
    "doc_type": "optional: the kind of document; normally worked out for you",
    "id": "optional: the archive id; normally worked out for you",
}
# Collections with their own top-level directory. Everything else from a public body goes
# under sources/<topic>/, as the existing holdings do.
MEETING_COLLECTIONS = {
    "council-captions": ("council_captions", "captions"),
    "annotated-agendas": ("annotated_agenda", "annotated-agenda"),
}
TOP_LEVEL_L0 = set(MEETING_COLLECTIONS) | {"clerk"}
# Agenda item reports: Council staff reports, and items from Council policy committees and City
# commissions. Filed under L0/staff-reports/ with the body named in the entry; the id is
# <body prefix>-<meeting date>[-<label>]-item-<NN>-<part>, as the existing holdings are.
COUNCIL = "Berkeley City Council"
STAFF_REPORT_DOC_TYPES = ("staff_report", "presentation", "agenda_material",
                          "supplemental_agenda_material")
ITEM_NUMBER_RE = re.compile(r"\bItem\s+0*(\d+)([A-Za-z])?\b", re.I)


class Problem(Exception):
    pass


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def load_manifest(root):
    d, out = os.path.join(root, "manifest"), {}
    if os.path.isdir(d):
        for n in sorted(os.listdir(d)):
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


def pdftotext_version():
    try:
        p = subprocess.run(["pdftotext", "-v"], capture_output=True, text=True)
    except FileNotFoundError:
        return None
    m = re.search(r"pdftotext version \S+", p.stderr + p.stdout)
    return m.group(0) if m else "pdftotext"


def slug(text, limit=70):
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if len(s) > limit:
        s = s[:limit].rsplit("-", 1)[0]
    return s


def pair_inbox(inbox):
    """Group inbox files into (name, document path, metadata path). Returns (pairs, problems)."""
    docs, metas, problems = {}, {}, []
    for n in sorted(os.listdir(inbox)):
        if n in IGNORED or n.startswith("."):
            continue
        p = os.path.join(inbox, n)
        if os.path.isdir(p):
            problems.append(f"inbox/{n}/ is a folder. Put files directly in inbox/, not in a folder.")
            continue
        stem, dot, ext = n.rpartition(".")
        if not dot:
            problems.append(f"inbox/{n} has no file extension (such as .pdf). Rename it.")
            continue
        ext = ext.lower()
        if ext == "json":
            metas[stem] = p
        elif ext in DOC_EXTENSIONS:
            if stem in docs:
                problems.append(f"inbox/{n} and inbox/{os.path.basename(docs[stem])} have the same "
                                "name. Give each document its own name.")
                continue
            docs[stem] = p
        else:
            problems.append(f"inbox/{n}: .{ext} files are not accepted here. Accepted types: "
                            f"{', '.join(sorted(DOC_EXTENSIONS))}. Ask the maintainer in an Issue "
                            "about anything else.")
    for stem in sorted(set(docs) - set(metas)):
        problems.append(f"inbox/{os.path.basename(docs[stem])} has no metadata file. Add "
                        f"inbox/{stem}.json (see inbox/README.md for a template).")
    for stem in sorted(set(metas) - set(docs)):
        problems.append(f"inbox/{stem}.json has no document with the same name. Upload the document "
                        f"as inbox/{stem}.pdf (the names before the dot must match).")
    return [(s, docs[s], metas[s]) for s in sorted(set(docs) & set(metas))], problems


def check_metadata(name, meta, manifest, where=None):
    """Return the placement (id, paths, doc_type) or raise Problem naming every issue found."""
    where = where or f"inbox/{name}.json"
    issues = []
    unknown = sorted(set(meta) - set(FIELDS))
    if unknown:
        issues.append(f"unknown field(s) {', '.join(unknown)}. Allowed fields: {', '.join(FIELDS)}.")

    def text(field):
        v = meta.get(field)
        if v is None:
            return None
        if not isinstance(v, str):
            issues.append(f'"{field}" must be text in quotes.')
            return None
        return v.strip() or None

    layer = text("layer")
    collection = text("collection")
    title = text("title")
    staff = collection == "staff-reports"
    for f in ("layer", "collection", "title") + (() if staff else ("document_date",)):
        if not meta.get(f):
            issues.append(f'"{f}" is missing: {FIELDS[f]}.')
    if layer and layer not in ("L0", "T0"):
        issues.append(f'"layer" is "{layer}"; it must be "L0" (made or published by a public body) '
                      'or "T0" (published by someone else, such as a newspaper).')

    ddate = text("document_date")
    wayback = text("wayback_url")
    if wayback and not WAYBACK_RE.match(wayback):
        issues.append(f'"wayback_url" must look like https://web.archive.org/web/20181026150626/http://...; '
                      f'got "{wayback}".')
    if ddate == "undated":
        if layer != "T0":
            issues.append('"document_date" may be "undated" only for T0 (third-party) documents; a public '
                          'body\'s document needs its printed date.')
        elif not (wayback or text("retrieved")):
            issues.append('an undated document needs "wayback_url" or "retrieved", so the date it was seen is '
                          'on record.')
    elif ddate and not DOC_DATE_RE.match(ddate):
        issues.append(f'"document_date" is "{ddate}"; write it as YYYY-MM-DD, e.g. 2026-09-12.')
    for f in ("retrieved", "meeting_date"):
        v = text(f)
        if v and not DATE_RE.match(v):
            issues.append(f'"{f}" is "{v}"; write it as YYYY-MM-DD, e.g. 2026-09-12.')

    url, note = text("source_url"), text("source_note")
    if not url and not note:
        issues.append('say where the document came from: add "source_url" (the web address it was '
                      'published at) or "source_note" (how and when you obtained it, e.g. "Requested '
                      'in person from the City Clerk; received 2026-09-12").')
    if url and not re.match(r"^https?://\S+$", url):
        issues.append(f'"source_url" must be a full web address starting with https://; got "{url}".')
    if url and "nextrequest.com" in url:
        issues.append("public-records releases from the City's request portal (NextRequest) are not "
                      "added through the inbox. Add the request to the queue by pull request instead; "
                      "the maintainer decides what is archived (README, \"Public-records requests\").")

    publisher, relevance = text("publisher"), text("relevance")
    if layer == "T0":
        if not publisher:
            issues.append('T0 documents need "publisher": who published it (e.g. "Berkeleyside, a '
                          'news organization").')
        if not relevance:
            issues.append('T0 documents need "relevance": the public decision or official it bears '
                          'on, in neutral terms. Say why it is here, not what it shows.')
    elif relevance:
        issues.append('"relevance" is only for T0 documents; remove it.')

    for f in ("body", "part"):
        if meta.get(f) is not None and not staff:
            issues.append(f'"{f}" is only for the staff-reports collection; remove it.')

    notices = meta.get("notices", [])
    if not isinstance(notices, list) or not all(isinstance(n, str) for n in notices):
        issues.append('"notices" must be a list of quoted texts, e.g. ["This information ..."].')
        notices = []

    # Placement: where the file goes, and how its id and doc_type are formed.
    place = None
    if collection and layer == "L0":
        if collection == "cpra":
            issues.append('"collection" is "cpra": public-records releases are archived from the '
                          "City's request portal once published, not through the inbox. Add the "
                          "request to the queue by pull request instead (README, \"Public-records "
                          "requests\"). Records an office gave you directly go in \"clerk\" or "
                          "\"sources/<topic>\" with a source_note.")
        elif collection in MEETING_COLLECTIONS:
            doc_type, suffix = MEETING_COLLECTIONS[collection]
            mdate, label = text("meeting_date"), text("meeting_label")
            if not mdate:
                issues.append(f'{collection} need "meeting_date" (YYYY-MM-DD).')
            if not label:
                issues.append(f'{collection} need "meeting_label", e.g. "regular" or "special".')
            if collection == "council-captions" and not notices:
                issues.append('council-captions need "notices": the City\'s disclaimer printed on the '
                              'transcript, copied word for word (e.g. "This information provided by a '
                              'Certified Realtime Reporter. The City of Berkeley cannot certify ...").')
            if mdate and label:
                place = {"l0": f"L0/{collection}", "l1": f"L1/{collection}", "doc_type": doc_type,
                         "collection": None, "meeting": True,
                         "id": f"bcc-{mdate}-{slug(label)}-{suffix}"}
        elif staff:
            place = staff_report_place(meta, text, manifest, issues)
        elif collection == "clerk":
            place = {"l0": "L0/clerk", "l1": "L1/clerk", "doc_type": "clerk_record",
                     "collection": "clerk", "prefix": "clerk"}
        elif collection.startswith("sources/") and TOPIC_RE.match(collection[8:]):
            topic = collection[8:]
            known = [e["doc_type"] for e in manifest.values()
                     if e["layer"] == "L0" and e.get("collection") == topic]
            place = {"l0": f"L0/sources/{topic}", "l1": f"L1/sources/{topic}",
                     "doc_type": max(set(known), key=known.count) if known else f"{topic}_document",
                     "collection": topic, "prefix": topic.replace("_", "-")}
        else:
            topics = sorted({e["collection"] for e in manifest.values()
                             if e["layer"] == "L0" and e.get("collection") and e.get("file")
                             and e["file"].startswith("L0/sources/")})
            issues.append(f'"collection" is "{collection}". For L0 use council-captions, '
                          f'annotated-agendas, staff-reports, clerk, or sources/<topic>. Existing topics: '
                          f'{", ".join("sources/" + t for t in topics)}. A new topic is lowercase '
                          'letters, digits and underscores, e.g. sources/parks.')
    elif collection and layer == "T0":
        if T0_COLLECTION_RE.match(collection):
            place = {"l0": f"T0/{collection}", "l1": "L1/third-party",
                     "doc_type": "third_party_publication", "collection": collection,
                     "prefix": collection.replace("_", "-")}
        else:
            issues.append(f'"collection" is "{collection}". For T0 use one lowercase word such as '
                          'press, campaigns or aahrc.')

    if place and "id" not in place:
        date_part = ddate if ddate and DOC_DATE_RE.match(ddate) else ""
        place["id"] = "-".join(p for p in (place["prefix"], date_part, slug(title or name)) if p)
    given_id = text("id")
    if given_id:
        if not ID_RE.match(given_id):
            issues.append(f'"id" "{given_id}" may contain only lowercase letters, digits and hyphens.')
        elif place:
            place["id"] = given_id
    if text("doc_type") and place:
        place["doc_type"] = text("doc_type")
    if layer == "L0" and not publisher:
        place = place and dict(place, warning=f'{where}: "publisher" is recommended for L0, e.g. '
                                              '"City of Berkeley — City Clerk".')
    if issues:
        raise Problem("\n".join(f"{where}: {i}" for i in issues))
    return place


def body_prefix(body, manifest):
    """The id prefix for a body: the one its existing staff-report entries use, else bcc for the
    Council, bcc-<name> for a Council committee, and the body's name for anything else."""
    known = []
    for e in manifest.values():
        if e.get("collection") == "staff_reports" and e.get("body") == body and e.get("meeting_date"):
            head, sep, _ = e["id"].partition(f"-{e['meeting_date']}-")
            if sep:
                known.append(head)
    if known:
        return max(set(known), key=known.count)
    if body == COUNCIL:
        return "bcc"
    if body.startswith(COUNCIL + " "):
        return "bcc-" + slug(body[len(COUNCIL):])
    return slug(body)


def staff_report_place(meta, text, manifest, issues):
    """Placement for the staff-reports collection; appends to issues and returns None on error."""
    body, mdate, label, item = text("body"), text("meeting_date"), text("meeting_label"), text("item")
    doc_type = text("doc_type") or "staff_report"
    if not body:
        issues.append('staff-reports need "body": the body whose agenda it is on, e.g. '
                      '"Berkeley City Council", "Planning Commission".')
    if not mdate:
        issues.append('staff-reports need "meeting_date" (YYYY-MM-DD).')
    if body == COUNCIL and not label:
        issues.append('Council staff-reports need "meeting_label", e.g. "regular" or "special".')
    m = ITEM_NUMBER_RE.search(item or "")
    if not m:
        issues.append('staff-reports need "item": the agenda item as printed, containing "Item" and '
                      'its number, e.g. "Consent Calendar Item 12" or "Item 7B".')
    if doc_type not in STAFF_REPORT_DOC_TYPES:
        issues.append(f'"doc_type" is "{doc_type}"; for staff-reports use one of '
                      f'{", ".join(STAFF_REPORT_DOC_TYPES)}.')
    part = text("part") or doc_type.replace("_", "-")
    if not ID_RE.match(part):
        issues.append(f'"part" "{part}" may contain only lowercase letters, digits and hyphens.')
    if not (body and mdate and DATE_RE.match(mdate) and m) or (body == COUNCIL and not label):
        return None
    when = mdate + (f"-{slug(label)}" if body == COUNCIL and label != "regular" else "")
    number = f"{int(m.group(1)):02d}{(m.group(2) or '').lower()}"
    return {"l0": "L0/staff-reports", "l1": "L1/staff-reports", "doc_type": doc_type,
            "collection": "staff_reports", "staff": True,
            "id": f"{body_prefix(body, manifest)}-{when}-item-{number}-{part}"}


def build_entry(meta, place, digest, size, contributor, today, ext):
    layer = meta["layer"]
    e = {"id": place["id"], "layer": layer, "doc_type": place["doc_type"]}
    if place["collection"]:
        e["collection"] = place["collection"]
    if place.get("meeting"):
        e.update(body="Berkeley City Council", meeting_date=meta["meeting_date"],
                 meeting_label=meta["meeting_label"])
    if place.get("staff"):
        e["body"] = meta["body"].strip()
    for f in ("publisher", "relevance"):
        if meta.get(f):
            e[f] = meta[f].strip()
    e["title"] = meta["title"].strip()
    if meta.get("document_date"):
        e["document_date"] = meta["document_date"].strip()
    if place.get("staff"):
        e["meeting_date"] = meta["meeting_date"]
        if meta.get("meeting_label"):
            e["meeting_label"] = meta["meeting_label"].strip()
    elif meta.get("meeting_date") and not place.get("meeting"):
        e["meeting_date"] = meta["meeting_date"]
    if meta.get("item"):
        e["item"] = str(meta["item"]).strip()
    e.update(file=f"{place['l0']}/{place['id']}.{ext}", sha256=digest, bytes=size,
             source_url=(meta.get("source_url") or "").strip() or None)
    if meta.get("source_note"):
        e["source_note"] = meta["source_note"].strip()
    e.update(retrieved=meta.get("retrieved") or None, archived=today,
             wayback_url=(meta.get("wayback_url") or "").strip() or None,
             notices=list(meta.get("notices") or []), contributed_by=contributor)
    return e


def extract_text(path):
    """Return pdftotext -layout output as bytes, or None if pdftotext is unavailable/fails."""
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "t.txt")
        try:
            p = subprocess.run(["pdftotext", "-layout", path, out], capture_output=True)
        except FileNotFoundError:
            return None
        if p.returncode != 0 or not os.path.exists(out):
            return None
        with open(out, "rb") as f:
            return f.read()


def write_l1(root, entry, place, local_path, tool, notes):
    """Extract L1 text from the original at local_path and write it with its manifest entry.
    Returns the L1 id, or a short reason there is none (also appended to notes)."""
    if not local_path.lower().endswith(".pdf"):
        return "not a PDF; no text layer extracted"
    if not tool:
        notes.append(f"{entry['id']}: pdftotext not installed; no L1 text. Install poppler and "
                     "run again, or let the GitHub Action do it.")
        return "pdftotext not installed; no L1 text"
    text = extract_text(local_path)
    if text is None or len(text.split()) < MIN_WORDS:
        notes.append(f"{entry['id']}: no text layer; needs OCR (with a recorded command, by the "
                     "maintainer).")
        return "no text layer; needs OCR"
    tid = entry["id"] + "-text"
    trel = f"{place['l1']}/{tid}.txt"
    os.makedirs(os.path.join(root, place["l1"]), exist_ok=True)
    with open(os.path.join(root, trel), "wb") as f:
        f.write(text)
    t = {"id": tid, "layer": "L1", "doc_type": entry["doc_type"] + "_text"}
    for k in ("collection", "body", "meeting_date", "meeting_label"):
        if k in entry:
            t[k] = entry[k]
    t.update(title=entry["title"], file=trel, sha256=hashlib.sha256(text).hexdigest(),
             bytes=len(text), derived_from={"id": entry["id"], "sha256": entry["sha256"]},
             extraction={"tool": tool, "command": f"pdftotext -layout <{entry['layer']}> <L1>"},
             notices=list(entry["notices"]), contributed_by=entry["contributed_by"],
             archived=entry["archived"])
    write_json(os.path.join(root, "manifest", tid + ".json"), t)
    return tid


def process(root, contributor, today=None, log=print):
    """Process the inbox under `root`. Returns a result dict; raises Problem (changing nothing)
    if any contribution is invalid."""
    today = today or datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    inbox = os.path.join(root, "inbox")
    result = {"added": [], "notes": [], "warnings": []}
    if not os.path.isdir(inbox):
        return result
    pairs, problems = pair_inbox(inbox)
    manifest = load_manifest(root)
    by_sha = {e["sha256"]: i for i, e in manifest.items()}
    plans, seen_ids, seen_sha = [], {}, {}

    for name, doc, metapath in pairs:
        docname = os.path.basename(doc)
        size = os.path.getsize(doc)
        if size > MAX_IN_GIT:
            problems.append(
                f"inbox/{docname} is {size / 1048576:.1f} MB. Files over 25 MB cannot go into the "
                "repository. Delete it from this branch and open a \"Large file\" Issue with its "
                "source URL or a shared-drive link; the maintainer stores large files outside git "
                "(README, \"Large files\").")
            continue
        try:
            with open(metapath, encoding="utf-8") as f:
                meta = json.load(f)
        except json.JSONDecodeError as x:
            problems.append(f"inbox/{name}.json is not valid JSON (line {x.lineno}, column {x.colno}: "
                            f"{x.msg}). Check for a missing comma or quote; compare with the template "
                            "in inbox/README.md.")
            continue
        except UnicodeDecodeError:
            problems.append(f"inbox/{name}.json is not a text file. Save it as plain UTF-8 text.")
            continue
        if not isinstance(meta, dict):
            problems.append(f"inbox/{name}.json must be one {{ ... }} object; see inbox/README.md.")
            continue
        try:
            place = check_metadata(name, meta, manifest)
        except Problem as x:
            problems.append(str(x))
            continue
        if place.get("warning"):
            result["warnings"].append(place["warning"])
        digest = sha256_file(doc)
        ext = docname.rpartition(".")[2].lower()
        mid = place["id"]
        if digest in by_sha:
            problems.append(f"inbox/{docname} is already in the archive as {by_sha[digest]} (same "
                            "fingerprint). Remove it from this pull request.")
            continue
        if digest in seen_sha:
            problems.append(f"inbox/{docname} is the same file as inbox/{seen_sha[digest]}. "
                            "Remove one of them.")
            continue
        clash = [i for i in (mid, mid + "-text") if i in manifest]
        if clash or mid in seen_ids:
            other = clash[0] if clash else f"inbox/{seen_ids[mid]}"
            problems.append(f"inbox/{docname} would get the id {mid}, which is already used by "
                            f"{other}. Add an \"id\" field with a different id (lowercase letters, "
                            "digits and hyphens), or check that this is not a copy.")
            continue
        entry = build_entry(meta, place, digest, size, contributor, today, ext)
        if os.path.exists(os.path.join(root, entry["file"])):
            problems.append(f"inbox/{docname}: {entry['file']} already exists. Add an \"id\" field "
                            "with a different id.")
            continue
        seen_ids[mid], seen_sha[digest] = docname, docname
        plans.append((doc, metapath, entry, place))

    if problems:
        raise Problem("\n".join(problems))

    tool = pdftotext_version() if any(p[2]["file"].endswith(".pdf") for p in plans) else None
    for doc, metapath, entry, place in plans:
        dest = os.path.join(root, entry["file"])
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.move(doc, dest)
        os.remove(metapath)
        write_json(os.path.join(root, "manifest", entry["id"] + ".json"), entry)
        item = {"id": entry["id"], "file": entry["file"], "sha256": entry["sha256"],
                "bytes": entry["bytes"], "text": None}
        result["added"].append(item)
        item["text"] = write_l1(root, entry, place, dest, tool, result["notes"])
    return result


def contributor_default():
    if os.environ.get("GITHUB_ACTOR"):
        return os.environ["GITHUB_ACTOR"]
    try:
        p = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True, cwd=ROOT)
        return p.stdout.strip() or None
    except FileNotFoundError:
        return None


def summary_markdown(result=None, problem=None):
    if problem:
        lines = ["**The inbox could not be processed.** Nothing was changed. Fix these and push "
                 "again (on the website: open the file, click the pencil, edit, commit):", ""]
        lines += [f"- {p}" for p in str(problem).splitlines()]
        return "\n".join(lines) + "\n"
    if not result["added"]:
        return "The inbox is empty; nothing to process.\n"
    lines = [f"**Added {len(result['added'])} document(s) to the archive.**", "",
             "| id | file | sha256 | text layer |", "|---|---|---|---|"]
    for a in result["added"]:
        lines.append(f"| `{a['id']}` | `{a['file']}` | `{a['sha256']}` | {a['text']} |")
    for n in result["warnings"] + result["notes"]:
        lines.append(f"\n- {n}")
    lines.append("\nThe maintainer reviews and merges. Check the title, date and source above.")
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--contributor", help="GitHub username recorded as contributed_by "
                    "(default: $GITHUB_ACTOR, else git config user.name)")
    ap.add_argument("--root", default=ROOT, help="archive root (default: this repository)")
    ap.add_argument("--summary-file", help="also write a Markdown summary here (for the PR comment)")
    a = ap.parse_args(argv)
    contributor = a.contributor or contributor_default()
    if not contributor:
        ap.error("pass --contributor <your GitHub username>")
    try:
        result = process(a.root, contributor)
    except Problem as x:
        print("NOT PROCESSED. Nothing was changed. Fix these and run again:", file=sys.stderr)
        for line in str(x).splitlines():
            print("ERROR", line, file=sys.stderr)
        if a.summary_file:
            with open(a.summary_file, "w") as f:
                f.write(summary_markdown(problem=x))
        return 1
    for w in result["warnings"]:
        print("WARNING", w)
    for item in result["added"]:
        print(f"added {item['id']}  {item['file']}  sha256 {item['sha256']}  text: {item['text']}")
    for n in result["notes"]:
        print("note:", n)
    print(f"{len(result['added'])} document(s) processed; contributed_by {contributor}")
    if a.summary_file:
        with open(a.summary_file, "w") as f:
            f.write(summary_markdown(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
