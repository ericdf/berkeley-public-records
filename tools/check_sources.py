#!/usr/bin/env python3
"""Download newly added public-body documents again from their source and compare fingerprints.

For each manifest entry added between two commits that is L0, kept in git, and has a source_url on
an approved source host (registry/source-hosts.json), this downloads the source_url and checks
that its sha256 matches the entry. It catches a file that differs from what the public body
serves: a wrong file, a corrupted one, or an altered one.

  python3 tools/check_sources.py --base <commit> [--head HEAD]

A mismatch is an error. A download that fails (the host is down, or refuses automated requests)
is only a warning, so an outage elsewhere never blocks a contribution. Skipped: web pages
(.htm/.html, which change on every request), public-records portal documents (their source_url
is the request page), and external blobs (the daily archive job checks those).

Runs in CI on every push to main and every pull request. Standard library only.
"""
import argparse, hashlib, json, os, subprocess, sys, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import source_hosts  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAX_BYTES = 25 * 1024 * 1024 + 1   # a file in git is at most 25 MB; anything larger cannot match
SKIP_EXT = (".htm", ".html")
NULL_SHA = "0" * 40


def added_entries(root, base, head):
    """Manifest ids added between base and head, or None when base is not a known commit."""
    if not base or base == NULL_SHA:
        return None
    ok = subprocess.run(["git", "-C", root, "cat-file", "-e", base + "^{commit}"], capture_output=True)
    if ok.returncode != 0:
        return None
    out = subprocess.run(["git", "-C", root, "diff", "--name-only", "--diff-filter=A", base, head, "--", "manifest/"],
                         check=True, capture_output=True, text=True).stdout.split()
    return sorted(os.path.basename(p)[:-5] for p in out if p.endswith(".json"))


def fetch_sha256(url):
    """(sha256, bytes) of what url serves now. Raises on any failure."""
    req = urllib.request.Request(url, headers={"User-Agent": "berkeley-public-records check_sources"})
    h, n = hashlib.sha256(), 0
    with urllib.request.urlopen(req, timeout=60) as r:
        for block in iter(lambda: r.read(1 << 20), b""):
            n += len(block)
            if n > MAX_BYTES:
                raise ValueError("the source serves more than 25 MB")
            h.update(block)
    return h.hexdigest(), n


def check(root, ids, hosts, fetch=fetch_sha256):
    """Returns (errors, warnings, checked ids)."""
    errors, warnings, checked = [], [], []
    for i in ids:
        path = os.path.join(root, "manifest", i + ".json")
        if not os.path.exists(path):
            continue
        with open(path) as f:
            e = json.load(f)
        url = e.get("source_url")
        if (e.get("layer") != "L0" or not e.get("file") or e.get("cpra")
                or str(e["file"]).lower().endswith(SKIP_EXT) or not source_hosts.approved(url, hosts)):
            continue
        try:
            digest, size = fetch(url)
        except Exception as x:  # any failure to download is a warning, never a block
            warnings.append(f"{i}: could not download {url} to check it ({x})")
            continue
        checked.append(i)
        if digest != e["sha256"]:
            errors.append(f"{i}: {url} now serves a different file (sha256 {digest}, {size} bytes) from the one "
                          f"archived (sha256 {e['sha256']}, {e['bytes']} bytes). Check that the archived file is "
                          "the one the public body published. If the body has since replaced it, archive the "
                          "current file as a new version (README, \"Rules by layer\") and keep this one.")
    return errors, warnings, checked


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", required=True, help="the commit before the change")
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--root", default=ROOT)
    a = ap.parse_args(argv)
    ids = added_entries(a.root, a.base, a.head)
    if ids is None:
        print(f"WARNING base {a.base!r} is not a known commit; no sources checked")
        return 0
    errors, warnings, checked = check(a.root, ids, source_hosts.load(a.root))
    for w in warnings:
        print("WARNING", w)
    for x in errors:
        print("ERROR", x)
    print(f"{len(ids)} entries added; {len(checked)} checked against their source; {len(errors)} errors")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
