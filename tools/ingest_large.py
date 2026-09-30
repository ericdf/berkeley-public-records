#!/usr/bin/env python3
"""Archive a file over 25 MB offered in a "Large file" Issue, as an external blob.

Run by the maintainer (or an agent at the maintainer's direction), never by a GitHub Action.

  python3 tools/ingest_large.py <issue number> [--collection sources/<topic>]
      Dry run (the default): reads the Issue, downloads the file to the staging directory
      (from the City URL, or from Google Drive through the rclone remote bpr-drive:),
      fingerprints it, refuses duplicates and id collisions, and prints what it would do.
      Uploads nothing and changes nothing in the repository.

  python3 tools/ingest_large.py <issue number> --upload [--open-pr]
      Also: copies the file to bpr-drive:berkeley-public-records-blobs/<id>.<ext> and verifies
      that copy by downloading it; uploads it to the Internet Archive as item
      berkeley-public-records-<id> (PUBLIC AND PERMANENT: only with the maintainer's explicit
      approval for this file); writes manifest/<id>.json with "file": null and both copies; writes
      L1 text when the PDF has a text layer. With --open-pr it also commits, pushes, and opens a
      pull request that closes the Issue; otherwise it prints those commands.

Requires: gh (signed in), rclone with remote bpr-drive: signed in as the archive account, and for
--upload the Internet Archive CLI `ia` configured for the archive account. See README, "Large files".
"""
import argparse, datetime, json, os, re, shutil, subprocess, sys, tempfile, urllib.parse, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import process_inbox as pi  # noqa: E402

ROOT = pi.ROOT
REPO = "ericdf/berkeley-public-records"
REMOTE = "bpr-drive:"
DRIVE_FOLDER = "berkeley-public-records-blobs"
DEFAULT_STAGING = os.path.expanduser("~/berkeley-public-records-pending-blobs")
LABELS = {
    "Document title": "title", "Document date": "document_date", "Layer": "layer",
    "Publisher": "publisher", "City web address": "source_url", "Google Drive link": "drive_link",
    "How you obtained it": "source_note", "Relevance (T0 only)": "relevance",
    "Collection, if you know it": "collection", "Size, if you know it": "size",
    "Anything else": "notes",
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


def plan(root, issue, staging, collection=None, fetch=None, today=None):
    """Checks only: download, fingerprint, refuse duplicates. Returns the plan; changes nothing in
    the repository. `fetch(fields, dest_dir) -> local path` is injectable for tests."""
    today = today or datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    n = issue["number"]
    f = parse_issue_body(issue.get("body") or "")
    where = f"issue #{n}"
    meta = {k: f[k] for k in ("title", "document_date", "layer", "publisher", "source_url",
                              "source_note", "relevance", "collection") if f.get(k)}
    if collection:
        meta["collection"] = collection
    if not meta.get("collection"):
        raise Problem(f"{where}: no collection given; pass --collection (e.g. sources/budgets, clerk, press)")
    if not f.get("source_url") and not f.get("drive_link"):
        raise Problem(f"{where}: neither a City web address nor a Google Drive link; ask the contributor for one")
    if f.get("source_url") and not f.get("drive_link"):
        meta.setdefault("retrieved", today)
    manifest = pi.load_manifest(root)
    place = pi.check_metadata(f"issue-{n}", meta, manifest, where=where)

    work = os.path.join(staging, f"issue-{n}")
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
        raise Problem(f"{where}: id {mid} is already used; rerun after choosing another")
    if size <= pi.MAX_IN_GIT:
        raise Problem(f"{where}: the file is {size / 1048576:.1f} MB, not over 25 MB. Add it through "
                      f"the inbox instead (inbox/README.md); it was downloaded to {path}")
    staged = os.path.join(work, f"{mid}.{ext}")
    os.replace(path, staged)
    entry = pi.build_entry(meta, place, digest, size, issue["author"]["login"], today, ext)
    entry["file"] = None
    return {"issue": n, "entry": entry, "place": place, "staged": staged, "ext": ext,
            "ia_item": f"berkeley-public-records-{mid}", "drive_path": f"{DRIVE_FOLDER}/{mid}.{ext}"}


def describe(p):
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
        "With --upload this would:",
        f"  1. copy it to {REMOTE}{p['drive_path']} and verify the copy by downloading it",
        f"  2. upload it to the Internet Archive as item {p['ia_item']}  (PUBLIC AND PERMANENT)",
        f"  3. write manifest/{e['id']}.json (file: null, both copies) and L1 text if extractable",
        f"  4. open a pull request that closes #{p['issue']} (with --open-pr; otherwise print the commands)",
    ])


def ia_metadata(e):
    desc = (f"Berkeley public record archived by berkeley-public-records. sha256 {e['sha256']}. "
            f"Source: {e.get('source_url') or e.get('source_note')}")
    md = {"mediatype": "texts", "title": e["title"], "date": e["document_date"],
          "creator": e.get("publisher") or "", "subject": "Berkeley; public records",
          "description": desc}
    return [f"--metadata={k}:{v}" for k, v in md.items() if v]


def execute(root, p, run=subprocess.run, open_pr=False, today=None):
    """The --upload path. Every external step is checked; nothing is written to the manifest
    unless the Drive copy verifies and the Internet Archive accepts the upload."""
    today = today or datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    e, staged = p["entry"], p["staged"]
    remote_path = REMOTE + p["drive_path"]
    run(["rclone", "copyto", staged, remote_path], check=True)
    with tempfile.TemporaryDirectory() as td:
        back = os.path.join(td, "verify")
        run(["rclone", "copyto", remote_path, back], check=True)
        if pi.sha256_file(back) != e["sha256"]:
            raise Problem(f"the Drive copy at {remote_path} does not match sha256; not recorded")
    run(["ia", "upload", p["ia_item"], staged, "--retries=5", *ia_metadata(e)], check=True)
    e["blob"] = {"copies": [
        {"store": "internet_archive", "item": p["ia_item"], "path": os.path.basename(staged),
         "uploaded": today, "verified": None,
         "verify_note": "Upload accepted by the Internet Archive; sha256 to be checked once the item is public."},
        {"store": "google_drive", "location": p["drive_path"], "uploaded": today, "verified": today},
    ]}
    pi.write_json(os.path.join(root, "manifest", e["id"] + ".json"), e)
    notes = []
    tool = pi.pdftotext_version()
    text = pi.write_l1(root, e, p["place"], staged, tool, notes)
    files = [f"manifest/{e['id']}.json"]
    if text == e["id"] + "-text":
        files += [f"manifest/{text}.json", f"{p['place']['l1']}/{text}.txt"]
    for n in notes:
        print("note:", n)
    branch = f"large-file/{e['id']}"
    body = (f"Adds `{e['id']}` as an external blob ({e['bytes']} bytes, sha256 `{e['sha256']}`): "
            f"Google Drive copy verified {today}; Internet Archive item `{p['ia_item']}` uploaded, "
            f"to be verified once public.\n\nCloses #{p['issue']}\n")
    cmds = [["git", "switch", "-c", branch], ["git", "add", *files],
            ["git", "commit", "-m", f"Add {e['id']} as an external blob\n\nCloses #{p['issue']}"],
            ["git", "push", "-u", "origin", branch],
            ["gh", "pr", "create", "--repo", REPO, "--base", "main",
             "--title", f"Add {e['title'][:60]} (external blob)", "--body", body]]
    if open_pr:
        for c in cmds:
            run(c, check=True, cwd=root)
    else:
        print("\nWritten. To open the pull request:")
        for c in cmds:
            print("  " + " ".join(json.dumps(x) if " " in x or "\n" in x else x for x in c))
    return files


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("issue", type=int, help="the Large file Issue number")
    ap.add_argument("--collection", help="override or supply the collection, e.g. sources/budgets")
    ap.add_argument("--upload", action="store_true",
                    help="copy to Drive and upload to the Internet Archive (public, permanent)")
    ap.add_argument("--open-pr", action="store_true", help="with --upload: commit, push and open the PR")
    ap.add_argument("--staging", default=DEFAULT_STAGING, help="download directory, outside the repository")
    ap.add_argument("--issue-json", help="read the issue from this JSON file instead of gh (testing)")
    ap.add_argument("--root", default=ROOT)
    a = ap.parse_args(argv)
    if os.path.abspath(a.staging).startswith(os.path.abspath(a.root) + os.sep):
        ap.error("--staging must be outside the repository")
    if a.open_pr and not a.upload:
        ap.error("--open-pr needs --upload")
    issue = json.load(open(a.issue_json)) if a.issue_json else fetch_issue(a.issue)
    try:
        p = plan(a.root, issue, a.staging, a.collection)
    except Problem as x:
        print("REFUSED", x, file=sys.stderr)
        return 2
    print(describe(p))
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
