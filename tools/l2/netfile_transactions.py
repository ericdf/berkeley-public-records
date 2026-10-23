#!/usr/bin/env python3
"""Build an L2 NetFile transaction table from archived L0 NetFile Connect2 responses, as of a recorded cutoff.

    python3 tools/l2/netfile_transactions.py <recipe.json> [--check]

The recipe (tools/l2/recipes/<id>.json) names the L2 id and output path, the L0 window responses that
together cover the date range (each fits on one page, so nothing depends on paging), the cutoff, the
filings excluded because NetFile dates them after the cutoff (each backed by an archived filing-info
record), and the superseded originals restored because a post-cutoff amendment replaced them (each
backed by the amendment's filing-info record and a ShowSuperceded=true response).

Rows are deduplicated by NetFile transaction id, then by (filer, transaction type, contributor name
as filed, date, amount): a contribution reported on a late 497 and again on a 460 is returned once per filing.
Contributor names are replaced by name_key, the first 16 hex digits of the SHA-256 of the lowercased,
trimmed name, so donors are countable without listing private individuals; three mechanical flags
record the name tests downstream aggregation needs. Output is sorted and written with LF endings, so
the same inputs always give the same bytes. --check rebuilds and compares with the archived file.
"""
import csv, hashlib, io, json, os, re, sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ORG = re.compile(r"PAC|Committee|Union|Local \d|Association|Club|Party|LLC|Inc\b|Corporation|Fund\b")
COLUMNS = ["id", "filingId", "filerLocalId", "filerName", "date", "amount", "transactionType", "city", "state",
           "name_key", "name_is_org_like", "name_is_city_of_berkeley", "name_empty"]


def load(root, mid):
    with open(os.path.join(root, "manifest", mid + ".json")) as f:
        e = json.load(f)
    with open(os.path.join(root, e["file"]), "rb") as f:
        raw = f.read()
    if hashlib.sha256(raw).hexdigest() != e["sha256"]:
        raise SystemExit(f"{mid}: file does not match its manifest sha256")
    return e, json.loads(raw)


def when(s):
    """NetFile timestamps carry 7 fractional digits; datetime takes 6."""
    return datetime.fromisoformat(re.sub(r"(\.\d{6})\d+", r"\1", s))


def build(recipe, root=ROOT):
    cutoff = when(recipe["cutoff"])
    excluded = {}
    for mid in recipe["excluded_filing_info"]:
        _, info = load(root, mid)
        if not when(info["filingDate"]) > cutoff:
            raise SystemExit(f"{mid}: filing {info['filingId']} is dated {info['filingDate']}, not after the cutoff")
        excluded[info["filingId"]] = info
    rows = []
    for mid in recipe["windows"]:
        _, d = load(root, mid)
        if d["totalMatchingCount"] != len(d["results"]):
            raise SystemExit(f"{mid}: window holds {len(d['results'])} of {d['totalMatchingCount']} rows (paged)")
        rows += [r for r in d["results"] if r["filingId"] not in excluded]
    replaced = {i["amends"] for i in excluded.values() if i.get("amends")}
    for s in recipe["superseded"]:
        if s["filingId"] not in replaced:
            raise SystemExit(f"{s['filingId']}: not amended by an excluded filing")
        _, d = load(root, s["response"])
        rows += [r for r in d["results"] if r["filingId"] == s["filingId"]]
    by_id = {}
    for r in rows:
        by_id.setdefault(r["id"], r)
    dd = {}
    for r in by_id.values():
        dd.setdefault((r["filerName"], r["transactionType"], r.get("name") or "", r["date"], r["amount"]), r)
    out = []
    for r in dd.values():
        name = (r.get("name") or "").strip()
        out.append([r["id"], r["filingId"], r.get("filerLocalId") or "", r["filerName"], r["date"][:10],
                    repr(float(r["amount"])), str(r["transactionType"]), (r.get("city") or "").strip(),
                    (r.get("state") or "").strip(), hashlib.sha256(name.lower().encode()).hexdigest()[:16],
                    "1" if ORG.search(name) and not (r.get("occupation") or "").strip() else "0",
                    "1" if re.search(r"city of berkeley", name, re.I) else "0", "1" if not name else "0"])
    out.sort(key=lambda x: (x[4], x[1], x[0]))
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(COLUMNS)
    w.writerows(out)
    return buf.getvalue().encode("utf-8"), len(out)


def main(argv):
    with open(argv[1]) as f:
        recipe = json.load(f)
    data, n = build(recipe)
    dest = os.path.join(ROOT, recipe["file"])
    if "--check" in argv:
        with open(dest, "rb") as f:
            same = f.read() == data
        print(f"{recipe['id']}: {'reproduces' if same else 'DOES NOT reproduce'} ({n} rows)")
        sys.exit(0 if same else 1)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(data)
    print(f"{recipe['id']}: {n} rows, sha256 {hashlib.sha256(data).hexdigest()}, {len(data)} bytes -> {recipe['file']}")


if __name__ == "__main__":
    main(sys.argv)
