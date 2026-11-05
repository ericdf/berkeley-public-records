#!/usr/bin/env bash
# tools/wayback.sh — find or request Wayback Machine captures of manifest source URLs, and
# record each capture whose bytes are the archived document's.
#
# For every L0/T0 entry with a source_url and no wayback_url (or only the ids given):
#   1. list the Wayback Machine's captures of source_url (CDX API, status 200, newest first);
#   2. accept the newest capture whose bytes match the entry: by SHA-1 digest against the file
#      in git when there is one, otherwise by downloading the capture and comparing sha256;
#   3. with --save, when no capture matches, request one (Save Page Now) and check it again;
#   4. with --write, set wayback_url to https://web.archive.org/web/<timestamp>/<source_url>.
# A capture that exists but does not match the entry is never recorded: it is another version
# of the document. Without --write nothing in the repository changes.
#
# Usage:
#   tools/wayback.sh [--save] [--write] [--limit N] [--delay SECONDS] [ID ...]
#
# Afterwards: tools/validate.py runs on --write; review `git diff manifest/`, then commit on a
# branch and open a PR ("recording wayback_url is a manifest edit by PR", AGENTS.md).
# Captures requested with --save are public and permanent, like any visit to Save Page Now.
#
# Output, one tab-separated line per entry: id, outcome, detail. Outcomes:
#   recorded / found  a matching capture (recorded only with --write)
#   saved             no match before; Save Page Now made one that matches
#   mismatch          captures exist, none matches the entry's bytes
#   none              no capture, and --save not given (or the save did not produce one)
#   error             the Wayback Machine did not answer after retries
#
# WAYBACK_BASE overrides https://web.archive.org (tests point it at a local stub).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE="${WAYBACK_BASE:-https://web.archive.org}"
UA="berkeley-public-records tools/wayback.sh"
SAVE=0 WRITE=0 LIMIT=0 DELAY=5
IDS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --save) SAVE=1 ;;
        --write) WRITE=1 ;;
        --limit) LIMIT="$2"; shift ;;
        --delay) DELAY="$2"; shift ;;
        -h|--help) sed -n '2,32p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        -*) echo "unknown option $1 (see --help)" >&2; exit 2 ;;
        *) IDS+=("$1") ;;
    esac
    shift
done

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# id <TAB> source_url <TAB> sha256 <TAB> path of the file in git, or "-"
candidates() {
    python3 - "$ROOT" "$LIMIT" "${IDS[@]+"${IDS[@]}"}" <<'EOF'
import json, os, sys
root, limit, ids = sys.argv[1], int(sys.argv[2]), sys.argv[3:]
mdir = os.path.join(root, "manifest")
names = [i + ".json" for i in ids] if ids else sorted(os.listdir(mdir))
n = 0
for name in names:
    path = os.path.join(mdir, name)
    if not os.path.exists(path):
        sys.exit(f"no manifest entry {name[:-5]}")
    with open(path) as f:
        e = json.load(f)
    if e.get("layer") not in ("L0", "T0") or not e.get("source_url") or e.get("wayback_url"):
        if ids:
            print(f"{e['id']}: skipped (not L0/T0, no source_url, or already has wayback_url)",
                  file=sys.stderr)
        continue
    local = os.path.join(root, e["file"]) if e.get("file") else ""
    print("\t".join([e["id"], e["source_url"], e["sha256"],
                     local if local and os.path.exists(local) else "-"]))
    n += 1
    if limit and n >= limit:
        break
EOF
}

# GET with backoff on 429, 5xx and dropped connections. Returns 0 on 200, 1 on any other
# answer (404 and the like), 2 when the service never answered.
ia_get() {
    local url="$1" out="$2" code i
    for i in 1 2 3 4 5; do
        code="$(curl -sS -m 300 -A "$UA" -o "$out" -w '%{http_code}' "$url" 2>/dev/null)" || code=000
        case "$code" in
            200) return 0 ;;
            429|5??|000) sleep $((DELAY * 2 ** i)) ;;
            *) return 1 ;;
        esac
    done
    return 2
}

quote() { python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$1"; }

sha1_b32() {
    python3 -c 'import sys, hashlib, base64
print(base64.b32encode(hashlib.sha1(open(sys.argv[1], "rb").read()).digest()).decode())' "$1"
}

sha256_of() { python3 -c 'import sys, hashlib; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$1"; }

# Set FOUND_TS to the timestamp of the newest capture of $url whose bytes are the entry's (or
# empty), and CAPTURES to the number of captures seen. Returns 2 if the service did not answer.
FOUND_TS="" CAPTURES=0
find_capture() {
    local url="$1" sha="$2" local_file="$3" rows want ts digest n=0 rc=0
    FOUND_TS="" CAPTURES=0
    ia_get "$BASE/cdx/search/cdx?url=$(quote "$url")&output=json&fl=timestamp,digest&filter=statuscode:200" \
        "$TMP/cdx.json" || rc=$?
    if [[ $rc -eq 2 ]]; then return 2; fi
    if [[ $rc -ne 0 || ! -s "$TMP/cdx.json" ]]; then return 0; fi
    # newest first, header row dropped
    rows="$(python3 -c 'import json, sys
rows = json.load(open(sys.argv[1]))[1:]
for ts, digest in reversed(rows):
    print(ts, digest)' "$TMP/cdx.json")"
    CAPTURES="$(printf '%s' "$rows" | grep -c . || true)"
    [[ "$CAPTURES" -gt 0 ]] || return 0
    if [[ "$local_file" != "-" ]]; then
        want="$(sha1_b32 "$local_file")"
        while read -r ts digest; do
            if [[ "$digest" == "$want" ]]; then FOUND_TS="$ts"; return 0; fi
        done <<< "$rows"
        return 0
    fi
    # No file in git (a staged or external blob, or a reference-only entry): download the
    # newest few captures and compare sha256.
    while read -r ts digest; do
        n=$((n + 1)); [[ $n -le 3 ]] || break
        sleep "$DELAY"
        if ia_get "$BASE/web/${ts}id_/$url" "$TMP/capture" && [[ "$(sha256_of "$TMP/capture")" == "$sha" ]]; then
            FOUND_TS="$ts"; return 0
        fi
    done <<< "$rows"
}

record() {
    python3 - "$ROOT/manifest/$1.json" "$2" <<'EOF'
import json, sys
path, wayback = sys.argv[1], sys.argv[2]
with open(path) as f:
    e = json.load(f)
e["wayback_url"] = wayback
with open(path, "w") as f:
    json.dump(e, f, indent=2, ensure_ascii=False)
    f.write("\n")
EOF
}

while IFS=$'\t' read -r id url sha local_file; do
    outcome="" detail="" rc=0
    find_capture "$url" "$sha" "$local_file" || rc=$?
    ts="$FOUND_TS"
    if [[ $rc -eq 2 ]]; then
        outcome=error detail="no answer from $BASE"
    elif [[ -n "$ts" ]]; then
        outcome=found
    elif [[ $SAVE -eq 1 ]]; then
        sleep "$DELAY"
        if ia_get "$BASE/save/$url" "$TMP/save"; then
            # A new capture can take a minute or two to appear in the index.
            for wait in $((DELAY * 6)) $((DELAY * 12)) $((DELAY * 24)); do
                sleep "$wait"
                find_capture "$url" "$sha" "$local_file" || true
                ts="$FOUND_TS"
                [[ -z "$ts" ]] || break
            done
        fi
        if [[ -n "$ts" ]]; then outcome=saved
        elif [[ "$CAPTURES" -gt 0 ]]; then outcome=mismatch detail="$CAPTURES capture(s), none matches; the source may now serve another version"
        else outcome=none detail="Save Page Now made no capture"
        fi
    elif [[ "$CAPTURES" -gt 0 ]]; then
        outcome=mismatch detail="$CAPTURES capture(s), none matches the entry's bytes"
    else
        outcome=none detail="no capture; rerun with --save"
    fi
    if [[ -n "$ts" ]]; then
        detail="https://web.archive.org/web/$ts/$url"
        if [[ $WRITE -eq 1 ]]; then
            record "$id" "$detail"
            [[ "$outcome" == found ]] && outcome=recorded
        fi
    fi
    echo "$outcome" >> "$TMP/outcomes"
    printf '%s\t%s\t%s\n' "$id" "$outcome" "$detail"
    sleep "$DELAY"
done < <(candidates)

if [[ -s "$TMP/outcomes" ]]; then
    sort "$TMP/outcomes" | uniq -c | awk '{printf "%s%s %s", sep, $2, $1; sep=", "} END {print ""}' >&2
else
    echo "nothing to do" >&2
fi

if [[ $WRITE -eq 1 ]]; then
    python3 "$ROOT/tools/validate.py" | tail -1
fi
