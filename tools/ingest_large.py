#!/usr/bin/env python3
"""Archive a file over 25 MB offered in a "Large file" Issue, as an external blob.

Needs the archive's storage credentials, which this repository never holds: run by the maintainer
(or an agent at the maintainer's direction), or by the daily archive job from the ops repository,
never by a workflow in this repository. Approved contributors stage large files instead
(tools/process_inbox.py with staged_url; README, "Staged landing").

  python3 tools/ingest_large.py <issue number> [--collection sources/<topic>]
      [--meeting-date YYYY-MM-DD --meeting-label regular]
      Dry run (the default): reads the Issue, downloads the file to the staging directory
      (from the City URL, or from Google Drive through the rclone remote bpr-drive:),
      fingerprints it, refuses duplicates and id collisions, and prints what it would do.
      Uploads nothing and changes nothing in the repository.

  python3 tools/ingest_large.py --verify-ia <id> [<id> ...]   (or --verify-ia pending)
      Downloads each blob's Internet Archive copy, checks its sha256 against the manifest, and
      records `verified` on that copy. Run it once the item is public; the large-file pull request
      may be merged once its Drive copy is verified; tools/validate.py lists blobs still waiting.

  python3 tools/ingest_large.py <issue number> --upload [--open-pr]
      Also: copies the file to bpr-drive:berkeley-public-records-blobs/<id>.<ext> and verifies
      that copy by downloading it; uploads it to the Internet Archive as item
      berkeley-public-records-<id> (PUBLIC AND PERMANENT: only when the maintainer says so for
      this Issue); writes manifest/<id>.json with "file": null and both copies; writes
      L1 text when the PDF has a text layer, and for an agenda packet its L2 item index
      (tools/l2/packet_items.py). With --open-pr it also commits, pushes, and opens a
      pull request that closes the Issue; otherwise it prints those commands.

Requires: gh (signed in), rclone with remote bpr-drive: signed in as the archive account, and for
--upload the Internet Archive CLI `ia` configured for the archive account. See README, "Large files".
"""
import argparse, datetime, hashlib, json, os, re, shutil, subprocess, sys, tempfile, urllib.parse, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import process_inbox as pi  # noqa: E402

ROOT = pi.ROOT
REPO = "ericdf/berkeley-public-records"
REMOTE = "bpr-drive:"
DRIVE_FOLDER = "berkeley-public-records-blobs"
DEFAULT_STAGING = os.path.expanduser("~/berkeley-public-records-pending-blobs")
IA_PREFIX = "berkeley-public-records-"
IA_MAX = 80  # the Internet Archive refuses identifiers longer than 80 characters
IA_DOWNLOAD = "https://archive.org/download/{item}/{path}"
DRIVE_MARGIN = 1 << 30  # keep 1 GiB free on the archive account's Drive after any upload
LABELS = {
    "Document title": "title", "Document date": "document_date", "Layer": "layer",
    "Publisher": "publisher", "City web address": "source_url", "Google Drive link": "drive_link",
    "How you obtained it": "source_note", "Relevance (T0 only)": "relevance",
    "Collection, if you know it": "collection", "Size, if you know it": "size",
    "Anything else": "notes", "Archive id, if needed": "id",
}
Problem = pi.Problem


def parse_issue_body(body):
    """Parse a GitHub issue-form body ("### Label" then the answer) into {field: value or None}."""
    out, label, buf = {}, None, []

    def flush():
        if label in LABELS:
            v = "\n".join(buf).strip()
            out[LABELS[label]] = None if v in ("", "_No response_", "None") else v

    for line in body.replace("\r\n", "\n").split("\n"):
        if line.startswith("### "):
            flush()
            label, buf = line[4:].strip(), []
        else:
            buf.append(line)
    flush()
    if out.get("layer"):
        out["layer"] = out["layer"].split()[0]
    return out


def drive_file_id(link):
    m = re.search(r"/d/([A-Za-z0-9_-]{10,})", link) or re.search(r"[?&]id=([A-Za-z0-9_-]{10,})", link)
    if not m:
        raise Problem(f"cannot find a Google Drive file id in {link!r}; ask for the file's share link")
    return m.group(1)


def fetch_url(url, dest_dir):
    """Download url into dest_dir; returns the local path."""
    req = urllib.request.Request(url, headers={"User-Agent": "berkeley-public-records ingest_large"})
    with urllib.request.urlopen(req, timeout=120) as r:
        name = os.path.basename(urllib.parse.unquote(urllib.parse.urlparse(r.geturl()).path)) or "download"
        if "." not in name and "pdf" in (r.headers.get("Content-Type") or ""):
            name += ".pdf"
        path = os.path.join(dest_dir, name)
        with open(path, "wb") as f:
            shutil.copyfileobj(r, f, 1 << 20)
    return path


def fetch_drive(link, dest_dir, run=subprocess.run):
    """Copy a file shared with the archive account (it appears under "Shared with me")."""
    fid = drive_file_id(link)
    before = set(os.listdir(dest_dir))
    run(["rclone", "backend", "copyid", REMOTE, fid, dest_dir.rstrip("/") + "/"], check=True)
    new = sorted(set(os.listdir(dest_dir)) - before)
    if len(new) != 1:
        raise Problem(f"rclone copyid produced {new!r}; expected one file")
    return os.path.join(dest_dir, new[0])


def fetch_issue(number):
    p = subprocess.run(["gh", "issue", "view", str(number), "--repo", REPO,
                        "--json", "number,title,body,author"], capture_output=True, text=True, check=True)
    return json.loads(p.stdout)


def plan(root, issue, staging, collection=None, fetch=None, today=None, meeting=None):
    """Checks only: download, fingerprint, refuse duplicates. Returns the plan; changes nothing in
    the repository. `fetch(fields, dest_dir) -> local path` is injectable for tests. `meeting`
    ({"meeting_date", "meeting_label"}) supplies those fields for a meeting collection; without it
    they come from the City catalog when the Issue's City web address is one it lists."""
    f = parse_issue_body(issue.get("body") or "")
    return plan_fields(root, f, issue["number"], f"issue #{issue['number']}", issue["author"]["login"],
                       staging, collection=collection, fetch=fetch, today=today, meeting=meeting)


def plan_fields(root, f, key, where, author, staging, collection=None, fetch=None, today=None,
                meeting=None, extra=None):
    """plan() for fields from any source (an Issue, or the City catalog). `extra` is merged into
    the manifest entry (revision fields, wayback_url). `key` names the staging subdirectory."""
    today = today or datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    n = key
    meta = {k: f[k] for k in ("title", "document_date", "layer", "publisher", "source_url",
                              "source_note", "relevance", "collection", "id") if f.get(k)}
    if collection:
        meta["collection"] = collection
    for k, v in (meeting or {}).items():
        if v:
            meta[k] = v
    meta = pi.fill_meeting(root, meta)
    if not meta.get("collection"):
        raise Problem(f"{where}: no collection given; pass --collection (e.g. sources/budgets, clerk, press)")
    if not f.get("source_url") and not f.get("drive_link"):
        raise Problem(f"{where}: neither a City web address nor a Google Drive link; ask the contributor for one")
    if f.get("source_url") and not f.get("drive_link"):
        meta.setdefault("retrieved", today)
    manifest = pi.load_manifest(root)
    place = pi.check_metadata(f"issue-{n}", meta, manifest, where=where)

    work = os.path.join(staging, f"issue-{n}" if isinstance(n, int) else str(n))
    if os.path.isdir(work):
        shutil.rmtree(work)
    os.makedirs(work)
    if fetch:
        path = fetch(f, work)
    elif f.get("drive_link"):
        path = fetch_drive(f["drive_link"], work)
    else:
        path = fetch_url(f["source_url"], work)
    size = os.path.getsize(path)
    digest = pi.sha256_file(path)
    ext = path.rpartition(".")[2].lower() if "." in os.path.basename(path) else ""
    if not ext:
        with open(path, "rb") as fh:
            ext = "pdf" if fh.read(5) == b"%PDF-" else ""
    if ext not in pi.DOC_EXTENSIONS:
        raise Problem(f"{where}: downloaded {os.path.basename(path)}, an unsupported type")
    by_sha = {e["sha256"]: i for i, e in manifest.items()}
    if digest in by_sha:
        raise Problem(f"{where}: this file is already in the archive as {by_sha[digest]} (same sha256)")
    mid = place["id"]
    if mid in manifest or mid + "-text" in manifest:
        raise Problem(f"{where}: id {mid} is already used; add a distinct id under "
                      f"'Archive id, if needed' in the Issue and rerun")
    if size <= pi.MAX_IN_GIT and meta["collection"] not in pi.BLOB_ONLY_COLLECTIONS:
        raise Problem(f"{where}: the file is {size / 1048576:.1f} MB, not over 25 MB. Add it through "
                      f"the inbox instead (inbox/README.md); it was downloaded to {path}")
    staged = os.path.join(work, f"{mid}.{ext}")
    os.replace(path, staged)
    entry = pi.build_entry(meta, place, digest, size, author, today, ext)
    entry["file"] = None
    for k, v in (extra or {}).items():
        if v is not None:
            entry[k] = v
    return {"issue": n if isinstance(n, int) else None, "entry": entry, "place": place, "staged": staged,
            "ext": ext, "ia_item": ia_identifier(mid), "drive_path": f"{DRIVE_FOLDER}/{mid}.{ext}"}


PUBLISHER = "City of Berkeley — City Clerk Department"
MONTHS = ("January February March April May June July August September October November December").split()


def wayback_lookup(url):
    """The Wayback Machine capture of exactly url, if one exists (read-only availability API)."""
    try:
        q = "https://archive.org/wayback/available?url=" + urllib.parse.quote(url, safe="")
        with urllib.request.urlopen(q, timeout=60) as r:
            snap = json.load(r).get("archived_snapshots", {}).get("closest") or {}
    except (OSError, ValueError):
        return None
    w = snap.get("url")
    if not w or not snap.get("available"):
        return None
    w = w.replace("http://web.archive.org", "https://web.archive.org")
    return w if re.match(r"^https://web\.archive\.org/web/\d{14}/https?://\S+$", w) else None


def original_url(listed):
    """The address a revised packet's original had, by the City's naming: 'Revised ' removed."""
    return re.sub(r"Revised(%20| )", "", listed, count=1)


def plan_meeting(root, slug, staging, author, fetch=None, today=None, wayback=wayback_lookup):
    """Plans for one City-listed meeting's agenda packet, from catalog/city-council-agendas.
    A packet the City lists as revised is planned with its original when the original still
    downloads from its former address; the two are cross-linked (revision_of / revised_by). When
    it does not, the revised packet records why in version_note. A meeting whose listed packet
    is already in the manifest returns no plans, so an interrupted batch can be rerun."""
    today = today or datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    path = os.path.join(root, pi.CITY_MEETINGS)
    with open(path) as fh:
        m = next((e for e in json.load(fh)["entries"] if e["meeting"] == slug), None)
    if not m:
        raise Problem(f"{slug}: not in {pi.CITY_MEETINGS}; run tools/catalog_city_agendas.py")
    listed = m["city"].get("agenda_packet")
    if not listed:
        raise Problem(f"{slug}: the City lists no agenda packet for this meeting")
    manifest = pi.load_manifest(root)
    if any(e.get("source_url") == listed for e in manifest.values()):
        return []
    y, mo, d = m["date"].split("-")
    long_date = f"{MONTHS[int(mo) - 1]} {int(d)}, {y}"
    label = m["label"]
    base = f"bcc-{m['date']}-{pi.slug(label)}-agenda-packet"
    name = urllib.parse.unquote(listed).rsplit("/", 1)[-1].lower()
    revised = "revised" in name
    kind = "Agenda Packet" if "packet" in name else "Agenda"

    def fields(url, title, mid):
        return {"title": title, "document_date": m["date"], "layer": "L0", "publisher": PUBLISHER,
                "source_url": url, "collection": "agenda-packets", "id": mid}

    meeting = {"meeting_date": m["date"], "meeting_label": label}
    title = f"{kind}, Berkeley City Council {label.title()} Meeting, {long_date}"
    plans = []
    if not revised:
        plans.append(plan_fields(root, fields(listed, title, base), slug, slug, author, staging, fetch=fetch,
                                 today=today, meeting=meeting, extra={"wayback_url": wayback(listed)}))
        return plans
    orig = original_url(listed)
    rid = base + "-revised"
    have_orig = base in manifest
    orig_plan, note = None, None
    if not have_orig:
        try:
            orig_plan = plan_fields(
                root, fields(orig, title, base), slug + "-original", slug, author, staging, fetch=fetch,
                today=today, meeting=meeting,
                extra={"revised_by": [rid], "wayback_url": wayback(orig),
                       "source_note": (f"No longer linked from the City's agenda page, which links the revised "
                                       f"packet ({listed}); downloaded from source_url on {today}.")})
        except (OSError, urllib.error.URLError) as x:
            note = (f"The City lists only this revised packet. The original was not located: {orig} "
                    f"returned {getattr(x, 'code', None) or x} on {today}, and the Wayback Machine has no "
                    f"capture of that address.") if not wayback(orig) else (
                    f"The City lists only this revised packet. The original no longer downloads from {orig} "
                    f"({getattr(x, 'code', None) or x} on {today}); a Wayback capture exists at {wayback(orig)}.")
    extra = {"version_label": "Revised", "wayback_url": wayback(listed), "version_note": note,
             "revision_of": base if (orig_plan or have_orig) else None}
    rev = plan_fields(root, fields(listed, "Revised " + title, rid), slug + "-revised", slug, author, staging,
                      fetch=fetch, today=today, meeting=meeting, extra=extra)
    if orig_plan:
        plans.append(orig_plan)
    rev["link_existing"] = base if have_orig else None
    plans.append(rev)
    return plans


def ia_identifier(mid):
    """Internet Archive item for archive id mid: IA_PREFIX + mid when that fits in IA_MAX
    characters; otherwise mid is cut short and the first 8 hex digits of its sha256 are
    appended, so the item stays unique and the same id always gives the same item."""
    item = IA_PREFIX + mid
    if len(item) <= IA_MAX:
        return item
    tag = hashlib.sha256(mid.encode()).hexdigest()[:8]
    return (IA_PREFIX + mid)[:IA_MAX - len(tag) - 1].rstrip("-") + "-" + tag


def describe(p, upload=False):
    e = p["entry"]
    return "\n".join([
        f"id:        {e['id']}",
        f"title:     {e['title']}",
        f"layer:     {e['layer']}   collection: {e.get('collection')}",
        f"bytes:     {e['bytes']}  ({e['bytes'] / 1048576:.1f} MB)",
        f"sha256:    {e['sha256']}",
        f"source:    {e.get('source_url') or e.get('source_note')}",
        f"staged at: {p['staged']}",
        "",
        "Uploading now:" if upload else "With --upload this would:",
        f"  1. copy it to {REMOTE}{p['drive_path']} and verify the copy by downloading it",
        f"  2. upload it to the Internet Archive as item {p['ia_item']}  (PUBLIC AND PERMANENT)",
        f"  3. write manifest/{e['id']}.json (file: null, both copies) and L1 text if extractable",
        f"  4. open a pull request that closes #{p['issue']} (with --open-pr; otherwise print the commands)",
    ])


def ia_metadata(e):
    desc = (f"Berkeley public record archived by berkeley-public-records. sha256 {e['sha256']}. "
            f"Source: {e.get('source_url') or e.get('source_note')}")
    md = {"mediatype": "texts", "title": e["title"], "date": e.get("document_date") or e.get("meeting_date"),
          "creator": e.get("publisher") or "", "subject": "Berkeley; public records",
          "description": desc}
    return [f"--metadata={k}:{v}" for k, v in md.items() if v]


def ia_stream(url):
    """Yield the bytes at url in chunks, without writing them to disk."""
    req = urllib.request.Request(url, headers={"User-Agent": "berkeley-public-records verify"})
    with urllib.request.urlopen(req, timeout=300) as r:
        for block in iter(lambda: r.read(1 << 20), b""):
            yield block


def drive_space(run=subprocess.run):
    """(free, total) bytes on the archive account's Google Drive, from `rclone about`."""
    p = run(["rclone", "about", REMOTE, "--json"], check=True, capture_output=True, text=True)
    d = json.loads(p.stdout)
    return d["free"], d["total"]


def check_drive_space(size, run=subprocess.run):
    """Refuse an upload that would leave less than DRIVE_MARGIN free on the Drive."""
    free, total = drive_space(run)
    if size + DRIVE_MARGIN > free:
        raise Problem(f"Google Drive has {free / 2**30:.2f} GiB free of {total / 2**30:.0f} GiB; this "
                      f"{size / 2**20:.0f} MiB file would leave less than {DRIVE_MARGIN / 2**30:.0f} GiB. "
                      "Nothing was uploaded. Free space or enlarge the archive account's storage first.")
    return free, total


def pending_ia(root):
    """Ids of external blobs whose Internet Archive copy is not yet verified."""
    return sorted(i for i, e in pi.load_manifest(root).items()
                  for c in (e.get("blob") or {}).get("copies", [])
                  if c.get("store") == "internet_archive" and not c.get("verified"))


def _hash_stream(blocks):
    h, n = hashlib.sha256(), 0
    for block in blocks:
        h.update(block)
        n += len(block)
    return h, n


def ia_server_url(item, path, meta_url="https://archive.org/metadata/{item}"):
    """The file's address on the Internet Archive storage server holding the item, from the
    item's metadata, or None."""
    try:
        with urllib.request.urlopen(meta_url.format(item=item), timeout=60) as r:
            d = json.load(r)
    except (OSError, ValueError):
        return None
    if not (d.get("d1") and d.get("dir")) or not any(f.get("name") == path for f in d.get("files", [])):
        return None
    return f"https://{d['d1']}{d['dir']}/{urllib.parse.quote(path)}"


def verify_ia(root, mid, stream=ia_stream, today=None):
    """Check the Internet Archive copy of blob `mid` against its manifest sha256 and record the
    result. Raises Problem, changing nothing, if the copy is missing or does not match."""
    today = today or datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    path = os.path.join(root, "manifest", mid + ".json")
    if not os.path.exists(path):
        raise Problem(f"{mid}: no manifest entry")
    with open(path) as f:
        e = json.load(f)
    copy = next((c for c in (e.get("blob") or {}).get("copies", []) if c.get("store") == "internet_archive"), None)
    if not copy:
        raise Problem(f"{mid}: no Internet Archive copy recorded")
    url = IA_DOWNLOAD.format(item=copy["item"], path=urllib.parse.quote(copy["path"]))
    try:
        h, n = _hash_stream(stream(url))
    except OSError as x:
        # archive.org/download can keep answering 404 for a while after an item is public;
        # the item's storage server, named in its metadata, serves the same file.
        direct = ia_server_url(copy["item"], copy["path"])
        if not direct:
            raise Problem(f"{mid}: could not download {url} ({x}); the item may not be public yet")
        try:
            h, n = _hash_stream(stream(direct))
        except OSError as y:
            raise Problem(f"{mid}: could not download {url} ({x}) or {direct} ({y}); the item may not be "
                          "public yet")
        url = direct
    if h.hexdigest() != e["sha256"] or n != e["bytes"]:
        raise Problem(f"{mid}: the Internet Archive copy at {url} does not match the manifest "
                      f"({n} bytes, sha256 {h.hexdigest()}); not recorded")
    copy["verified"] = today
    copy.pop("verify_note", None)
    pi.write_json(path, e)
    return url


def branch_exists(root, branch, run=subprocess.run):
    """True if branch exists locally or on origin."""
    local = run(["git", "rev-parse", "--verify", "--quiet", "refs/heads/" + branch],
                cwd=root, capture_output=True)
    remote = run(["git", "ls-remote", "--exit-code", "--heads", "origin", branch],
                 cwd=root, capture_output=True)
    return local.returncode == 0 or remote.returncode == 0


def packet_index(root, text_id, contributor, today, notes):
    """Build the L2 item index for an agenda packet from its L1 text (tools/l2/packet_items.py).
    Returns the files written; a packet the indexer cannot parse is archived without one, with a note."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "l2"))
    import packet_items
    try:
        lid, n = packet_items.write_new(text_id, contributor, root, today)
    except SystemExit as x:
        notes.append(f"{text_id}: no packet item index ({x}); archive the packet and resolve by hand")
        return []
    notes.append(f"{lid}: {n} agenda items indexed")
    with open(os.path.join(root, "manifest", lid + ".json")) as f:
        e = json.load(f)
    return [f"manifest/{lid}.json", e["file"], e["extraction"]["recipe"]]


def execute(root, p, run=subprocess.run, open_pr=False, today=None):
    """The --upload path for one Issue. Every external step is checked; nothing is written to the
    manifest unless the Drive copy verifies and the Internet Archive accepts the upload."""
    today = today or datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    e = p["entry"]
    branch = f"large-file/{e['id']}"
    if open_pr and branch_exists(root, branch, run):
        raise Problem(f"branch {branch} already exists, locally or on origin: this file may already have "
                      f"a pull request. Check before uploading again; nothing was uploaded.")
    notes = []
    files = upload_one(root, p, run, today, notes)
    for n in notes:
        print("note:", n)
    body = (f"Adds `{e['id']}` as an external blob ({e['bytes']} bytes, sha256 `{e['sha256']}`): "
            f"Google Drive copy verified {today}; Internet Archive item `{p['ia_item']}` uploaded.\n\n"
            + DRAFT_NOTE.format(ids=e["id"]) + f"\n\nCloses #{p['issue']}\n")
    cmds = [["git", "fetch", "origin", "main"], ["git", "switch", "-c", branch, "origin/main"],
            ["git", "add", *files],
            ["git", "commit", "-m", f"Add {e['id']} as an external blob\n\nCloses #{p['issue']}"],
            ["git", "push", "-u", "origin", branch],
            ["gh", "pr", "create", "--repo", REPO, "--base", "main", "--draft",
             "--title", f"Add {e['title'][:60]} (external blob)", "--body", body]]
    finish(root, cmds, run, open_pr)
    return files


DRAFT_NOTE = ("**Draft until the Internet Archive copies are verified.** Once the items are public, run "
              "`python3 tools/ingest_large.py --verify-ia {ids}` on this branch, commit, push, and mark the "
              "pull request ready. The validator fails until every copy is verified.")


def finish(root, cmds, run, open_pr):
    if open_pr:
        for c in cmds:
            run(c, check=True, cwd=root)
    else:
        print("\nWritten. To open the pull request:")
        for c in cmds:
            print("  " + " ".join(json.dumps(x) if " " in x or "\n" in x else x for x in c))


def link_existing(root, original_id, revised_id):
    """Record revised_by on an already-archived original (a manifest metadata change)."""
    path = os.path.join(root, "manifest", original_id + ".json")
    with open(path) as fh:
        e = json.load(fh)
    e["revised_by"] = sorted(set(e.get("revised_by") or []) | {revised_id})
    pi.write_json(path, e)
    return f"manifest/{original_id}.json"


def execute_batch(root, plans, run=subprocess.run, open_pr=False, today=None, branch=None):
    """--upload for --meeting: upload every plan, then one draft pull request for all of them.
    With open_pr the branch is created first, from origin/main, so the files land on it."""
    today = today or datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    ids = [p["entry"]["id"] for p in plans]
    branch = branch or f"large-file/agenda-packets-{today}"
    if open_pr:
        if branch_exists(root, branch, run):
            raise Problem(f"branch {branch} already exists; nothing was uploaded. Pass --branch to name another.")
        run(["git", "fetch", "origin", "main"], check=True, cwd=root)
        run(["git", "switch", "-c", branch, "origin/main"], check=True, cwd=root)
    files, notes = [], []
    for k, p in enumerate(plans, 1):
        print(f"\n[{k}/{len(plans)}] {p['entry']['id']}", flush=True)
        files += upload_one(root, p, run, today, notes)
        if p.get("link_existing"):
            files.append(link_existing(root, p["link_existing"], p["entry"]["id"]))
    for n in notes:
        print("note:", n)
    lines = "\n".join(f"- `{p['entry']['id']}` ({p['entry']['bytes'] / 2**20:.1f} MiB) from {p['entry']['source_url']}"
                      for p in plans)
    body = (f"Adds {len(plans)} Council agenda packets as external blobs, from the City agenda page. "
            f"Google Drive copies verified {today}; Internet Archive items uploaded.\n\n{lines}\n\n"
            + DRAFT_NOTE.format(ids="pending") + "\n")
    cmds = [["git", "add", *files],
            ["git", "commit", "-m", f"Add {len(plans)} Council agenda packet{'s' if len(plans) != 1 else ''} as external blobs"],
            ["git", "push", "-u", "origin", branch],
            ["gh", "pr", "create", "--repo", REPO, "--base", "main", "--draft",
             "--title", f"Add {len(plans)} Council agenda packet{'s' if len(plans) != 1 else ''} (external "
                        f"blob{'s' if len(plans) != 1 else ''})", "--body", body]]
    finish(root, cmds, run, open_pr)
    return files


def upload_one(root, p, run, today, notes):
    """Drive copy (verified), Internet Archive upload, manifest entry, L1 text, packet index.
    Returns the repository files written."""
    e, staged = p["entry"], p["staged"]
    remote_path = REMOTE + p["drive_path"]
    free, total = check_drive_space(e["bytes"], run)
    print(f"\nGoogle Drive: {free / 2**30:.2f} GiB free of {total / 2**30:.0f} GiB; room for this file.")
    print(f"1. Copying to {remote_path} ...", flush=True)
    run(["rclone", "copyto", staged, remote_path], check=True)
    with tempfile.TemporaryDirectory() as td:
        back = os.path.join(td, "verify")
        run(["rclone", "copyto", remote_path, back], check=True)
        if pi.sha256_file(back) != e["sha256"]:
            raise Problem(f"the Drive copy at {remote_path} does not match sha256; not recorded")
    print("   Drive copy verified.", flush=True)
    print(f"2. Uploading to the Internet Archive as {p['ia_item']} ...", flush=True)
    run(["ia", "upload", p["ia_item"], staged, "--retries=5", *ia_metadata(e)], check=True)
    print("   Upload accepted.", flush=True)
    print(f"3. Writing manifest/{e['id']}.json and L1 text ...", flush=True)
    e["blob"] = {"copies": [
        {"store": "internet_archive", "item": p["ia_item"], "path": os.path.basename(staged),
         "uploaded": today, "verified": None,
         "verify_note": ("Upload accepted by the Internet Archive; sha256 to be checked once the item is "
                         f"public, with: python3 tools/ingest_large.py --verify-ia {e['id']}")},
        {"store": "google_drive", "location": p["drive_path"], "uploaded": today, "verified": today},
    ]}
    pi.write_json(os.path.join(root, "manifest", e["id"] + ".json"), e)
    tool = pi.pdftotext_version()
    text = pi.write_l1(root, e, p["place"], staged, tool, notes)
    files = [f"manifest/{e['id']}.json"]
    if text == e["id"] + "-text":
        files += [f"manifest/{text}.json", f"{p['place']['l1']}/{text}.txt"]
        if e["doc_type"] == "agenda_packet":
            files += packet_index(root, text, e["contributed_by"], today, notes)
    return files


def missing_packets(root, since):
    """Slugs of City-listed meetings on or after `since` whose listed packet is not archived."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import meeting_map
    data = meeting_map.build(root)
    listed = {e.get("source_url") for e in pi.load_manifest(root).values()}
    return [m["meeting"] for m in sorted(data["meetings"], key=lambda m: (m["date"], m["meeting"]))
            if m["date"] >= since and m["label"] not in meeting_map.NO_DOCUMENTS
            and m["city"].get("agenda_packet") and m["city"]["agenda_packet"] not in listed]


def main_meetings(a):
    slugs = missing_packets(a.root, a.since) if a.meeting == ["missing"] else a.meeting
    author = a.contributor or subprocess.run(["gh", "api", "user", "--jq", ".login"], capture_output=True,
                                             text=True, check=True).stdout.strip()
    plans, refused = [], []
    for slug in slugs:
        try:
            got = plan_meeting(a.root, slug, a.staging, author)
        except Problem as x:
            refused.append(str(x))
            continue
        for p in got:
            e = p["entry"]
            tag = (" [original of a revised packet]" if e.get("revised_by") else
                   f" [revised; original {'planned' if e.get('revision_of') else 'NOT LOCATED'}]"
                   if e.get("version_label") else "")
            print(f"{e['id']:<58} {e['bytes'] / 2**20:8.1f} MiB{tag}", flush=True)
        plans += got
    total = sum(p["entry"]["bytes"] for p in plans)
    print(f"\n{len(plans)} files, {total / 2**30:.2f} GiB, from {len(slugs)} meetings.")
    for r in refused:
        print("REFUSED", r, file=sys.stderr)
    try:
        free, size = drive_space()
        room = "room for them" if total + DRIVE_MARGIN <= free else "NOT ENOUGH ROOM"
        print(f"Google Drive: {free / 2**30:.2f} GiB free of {size / 2**30:.0f} GiB; {room}.")
    except (OSError, subprocess.CalledProcessError, ValueError, KeyError) as x:
        print(f"Google Drive free space unknown ({x}).")
    print("Every file listed would be uploaded to the Internet Archive (PUBLIC AND PERMANENT).")
    if not a.upload:
        print("\nDry run: nothing uploaded, nothing written to the repository.")
        return 1 if refused else 0
    if refused:
        print("STOPPED: resolve the refusals before uploading", file=sys.stderr)
        return 1
    try:
        execute_batch(a.root, plans, open_pr=a.open_pr, branch=a.branch)
    except (Problem, subprocess.CalledProcessError) as x:
        print("STOPPED", x, file=sys.stderr)
        return 1
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("issue", type=int, nargs="?", help="the Large file Issue number")
    ap.add_argument("--meeting", nargs="+", metavar="SLUG",
                    help="agenda packets of these City-listed meetings (catalog slugs), in place of an Issue; "
                         "'missing' for every listed meeting whose packet is not archived (with --since)")
    ap.add_argument("--since", default="0000-00-00", help="with --meeting missing: meetings on or after this date")
    ap.add_argument("--contributor", help="with --meeting: the GitHub login recorded as contributed_by "
                                          "(default: gh api user)")
    ap.add_argument("--branch", help="with --meeting --upload --open-pr: the branch for the batch")
    ap.add_argument("--verify-ia", nargs="+", metavar="ID",
                    help="verify these blobs' Internet Archive copies ('pending' for every unverified one)")
    ap.add_argument("--collection", help="override or supply the collection, e.g. sources/budgets, agenda-packets")
    ap.add_argument("--meeting-date", help="meeting collections: the meeting date, YYYY-MM-DD, when the City "
                                           "catalog cannot supply it")
    ap.add_argument("--meeting-label", help="meeting collections: e.g. regular, special")
    ap.add_argument("--upload", action="store_true",
                    help="copy to Drive and upload to the Internet Archive (public, permanent)")
    ap.add_argument("--open-pr", action="store_true", help="with --upload: commit, push and open the PR")
    ap.add_argument("--staging", default=DEFAULT_STAGING, help="download directory, outside the repository")
    ap.add_argument("--issue-json", help="read the issue from this JSON file instead of gh (testing)")
    ap.add_argument("--root", default=ROOT)
    a = ap.parse_args(argv)
    if a.verify_ia:
        ids = pending_ia(a.root) if a.verify_ia == ["pending"] else a.verify_ia
        failed = 0
        for mid in ids:
            try:
                print(f"{mid}: verified {verify_ia(a.root, mid)}", flush=True)
            except Problem as x:
                print("NOT VERIFIED", x, file=sys.stderr)
                failed += 1
        print(f"{len(ids) - failed} of {len(ids)} verified; commit the manifest changes on the blob's branch")
        return 1 if failed else 0
    if os.path.abspath(a.staging).startswith(os.path.abspath(a.root) + os.sep):
        ap.error("--staging must be outside the repository")
    if a.open_pr and not a.upload:
        ap.error("--open-pr needs --upload")
    if a.meeting:
        return main_meetings(a)
    if a.issue is None:
        ap.error("give a Large file Issue number, --meeting, or --verify-ia")
    issue = json.load(open(a.issue_json)) if a.issue_json else fetch_issue(a.issue)
    try:
        p = plan(a.root, issue, a.staging, a.collection,
                 meeting={"meeting_date": a.meeting_date, "meeting_label": a.meeting_label})
    except Problem as x:
        print("REFUSED", x, file=sys.stderr)
        return 2
    print(describe(p, upload=a.upload))
    try:
        free, total = drive_space()
        room = "room for it" if p["entry"]["bytes"] + DRIVE_MARGIN <= free else "NOT ENOUGH ROOM; --upload will refuse"
        print(f"\nGoogle Drive: {free / 2**30:.2f} GiB free of {total / 2**30:.0f} GiB; {room}.")
    except (OSError, subprocess.CalledProcessError, ValueError, KeyError) as x:
        print(f"\nGoogle Drive free space unknown ({x}); --upload checks it before copying.")
    if not a.upload:
        print("\nDry run: nothing uploaded, nothing written to the repository.")
        return 0
    try:
        execute(a.root, p, open_pr=a.open_pr)
    except (Problem, subprocess.CalledProcessError) as x:
        print("STOPPED", x, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
