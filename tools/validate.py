#!/usr/bin/env python3
"""Validate the archive: manifest completeness, fingerprints, provenance, and L3 rules.

Standard library only, so any contributor can run it:  python3 tools/validate.py
Exit status 1 on any error. See README.md for the rules enforced here.
"""
import hashlib, json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAYERS = ("L0", "L1", "L2", "L3")
MAX_IN_GIT = 25 * 1024 * 1024
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
LEVELS = {"verified", "cued", "inferred"}
L3_NOTICE = ("Speaker attribution in this layer is inferred, not authoritative. It was produced by "
             "software and volunteers, not by the City. Verify any attribution against the meeting "
             "video before relying on it.")
REQUIRED = {"id", "layer", "doc_type", "title", "file", "sha256", "bytes", "archived", "contributed_by"}
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

def main():
    manifest, files_listed = {}, {}
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
        if e["layer"] == "L0" and not e.get("source_url") and not e.get("source_note"):
            err(where, "L0 needs source_url or source_note")
        if e["layer"] in ("L1", "L2", "L3"):
            if "derived_from" not in e:
                err(where, f"{e['layer']} needs derived_from (null only with a source_note)")
            elif e["derived_from"] is None and not e.get("source_note"):
                err(where, "derived_from is null without a source_note explaining why")
        if e["layer"] == "L1" and e.get("derived_from") and not (e.get("extraction") or {}).get("command"):
            err(where, "L1 needs extraction.command")
        if e["file"] is None:
            copies = (e.get("blob") or {}).get("copies") or []
            if len(copies) < 2:
                err(where, "external blob needs at least two independent copies")
            if e["layer"] != "L0":
                err(where, "only L0 files may be external blobs")
        else:
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
                if size > MAX_IN_GIT and e["layer"] == "L0":
                    err(where, "L0 file over 25 MB must be an external blob")
        manifest[e["id"]] = e

    # derived_from must point at a real entry with a matching fingerprint
    for i, e in manifest.items():
        d = e.get("derived_from")
        if d:
            src = manifest.get(d.get("id"))
            if not src:
                err(f"manifest/{i}.json", f"derived_from {d.get('id')!r} not in manifest")
            elif src["sha256"] != d.get("sha256"):
                err(f"manifest/{i}.json", "derived_from sha256 does not match the source entry")

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

    for e in errors:
        print("ERROR", e)
    n = len(manifest)
    print(f"{n} manifest entries; {len(errors)} errors")
    sys.exit(1 if errors else 0)

if __name__ == "__main__":
    main()
