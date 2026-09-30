#!/usr/bin/env python3
"""Watch the City of Berkeley NextRequest portal and queue requests for review.

Writes queue/nextrequest/<id>.json with request METADATA ONLY. Nothing is downloaded and
nothing is inducted: every entry waits for a decision by the maintainer (see README.md,
"Public-records requests"). rank_hints are used only to sort the review list.

  python3 tools/nextrequest_watch.py [--since YYYY-MM-DD] [--max-pages N] [--dry-run]

--since defaults to the newest request_date already in the queue, or 2024-12-10.
Re-running refreshes state, visibility, departments and document_count for existing
entries and never changes a decision. Entries that are not settled (still open, not
published, or decided "watching") are re-checked individually on every run. An entry
decided "watching" whose public document count rises returns to status "candidate" with
a note, so the maintainer sees it again.

Standard library only.
"""
import argparse, datetime, json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nextrequest_client as nr  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKFILL_START = "2024-12-10"
EXCERPT_CHARS = 600

# Sorting hints only. A hit never includes or excludes anything; the maintainer decides.
DEPARTMENT_HINTS = {
    "City Council", "City Manager's Office", "City Mayor's Office", "City Auditor Office",
    "City Clerk", "Finance", "Multi-Department", "Police Department", "Public Works",
    "Public Works - Engineering", "Public Works - Transportation", "Fire - Operations",
    "Fire - Office of Emergency Services", "Fire - Emergency Medical Services",
}
KEYWORD_HINTS = [
    "council", "budget", "audit", "contract", "agreement", "procurement", "purchase order",
    "invoice", "bid tab", "bid tally", "bid result", "bidder", "rfp", "rfq", "request for proposal",
    "capital", "grant", "measure", "bond", "expenditure", "payment", "funding", "fiscal",
    "policy", "policies", "use of force", "staffing", "overtime", "response time",
    "calls for service", "dispatch", "communications between", "emails between", "memo",
    "report", "consultant", "lease", "settlement", "claims",
]
NEGATIVE_HINTS = [
    "inspection history", "permit history", "my personnel", "personnel file", "employment record",
    "my own", "incident report", "collision report", "accident report", "certificate of occupancy",
    "code enforcement case", "environmental site", "phase i", "underground storage tank",
    "the following property", "hazardous substance", "apn", "ust", "hazmat", "dog",
]


def today():
    return datetime.date.today().isoformat()


def queue_dir(root):
    return os.path.join(root, "queue", "nextrequest")


def load_queue(root):
    d, out = queue_dir(root), {}
    if os.path.isdir(d):
        for n in sorted(os.listdir(d)):
            if n.endswith(".json"):
                with open(os.path.join(d, n)) as f:
                    e = json.load(f)
                out[e["id"]] = e
    return out


def save_entry(root, e):
    os.makedirs(queue_dir(root), exist_ok=True)
    with open(os.path.join(queue_dir(root), e["id"] + ".json"), "w") as f:
        json.dump(e, f, indent=2, ensure_ascii=False)
        f.write("\n")


def rank_hints(departments, text, document_count, state):
    low = text.lower()
    hit = lambda k: re.search(r"\b" + re.escape(k.strip()) + r"\b", low)
    deps = sorted(d for d in departments if d in DEPARTMENT_HINTS)
    kws = sorted({k.strip() for k in KEYWORD_HINTS if hit(k)})
    neg = sorted({k for k in NEGATIVE_HINTS if hit(k)})
    score = len(deps) + len(kws) - 2 * len(neg)
    if document_count:
        score += 2
    elif state == "Closed" and document_count == 0:
        score -= 3
    return {"departments": deps, "keywords": kws, "negative": neg, "score": score}


def auto_status(state, published):
    return "candidate" if state == "Closed" and published else "watching"


def build_entry(rid, request_date, state, visibility, departments, text, document_count, now):
    published = str(visibility).strip().lower() == "published"
    excerpt = nr.plain_text(text)
    # Form-letter requests put ~600 characters of boilerplate before the request itself.
    m = re.search(r"\bRecords requested:\s*", excerpt)
    if m and m.start() > 0:
        excerpt = "… " + excerpt[m.end():]
    if len(excerpt) > EXCERPT_CHARS:
        excerpt = excerpt[:EXCERPT_CHARS].rsplit(" ", 1)[0] + " …"
    return {
        "id": rid,
        "source": "nextrequest",
        "request_date": request_date,
        "state": state,
        "visibility": visibility,
        "published": published,
        "departments": departments,
        "request_text_excerpt": excerpt,
        "document_count": document_count,
        "url": nr.request_url(rid),
        "first_seen": now,
        "last_checked": now,
        "status": auto_status(state, published),
        "rank_hints": rank_hints(departments, excerpt, document_count, state),
        "notes": [],
        "decision": None,
    }


def refresh(entry, fresh, now):
    """Update observable fields of an existing entry from a freshly built one.
    Never touches decision, first_seen, or notes already written. Returns True if changed."""
    before = json.dumps(entry, sort_keys=True)
    old_docs = entry.get("document_count") or 0
    for k in ("state", "visibility", "published", "departments", "request_text_excerpt"):
        if fresh.get(k) is not None:
            entry[k] = fresh[k]
    if fresh.get("document_count") is not None:
        entry["document_count"] = fresh["document_count"]
    entry["rank_hints"] = rank_hints(entry["departments"], entry["request_text_excerpt"],
                                     entry["document_count"], entry["state"])
    dec = entry.get("decision")
    if dec is None:
        entry["status"] = auto_status(entry["state"], entry["published"])
    elif dec.get("status") == "watching":
        new_docs = entry.get("document_count") or 0
        if new_docs > old_docs and entry.get("status") == "watching":
            entry["status"] = "candidate"
            entry.setdefault("notes", []).append(
                f"{now}: re-surfaced; public documents rose from {old_docs} to {new_docs} "
                f"after the decision of {dec.get('decided_on')} to watch")
    changed = json.dumps(entry, sort_keys=True) != before
    if changed or fresh.get("_checked"):
        entry["last_checked"] = now
    return changed or bool(fresh.get("_checked"))


def settled(e):
    dec = e.get("decision") or {}
    if dec.get("status") in ("inducted", "rejected"):
        return True
    return e.get("state") == "Closed" and e.get("published") and dec.get("status") != "watching"


def count_public_docs(client, rid):
    try:
        docs = client.documents(rid)
    except nr.NotPublic:
        return None
    return sum(1 for d in docs["documents"] if nr.is_public_document(d))


def fresh_from_listing(client, r, known, fetch_docs):
    rid = r["id"]
    n_docs = count_public_docs(client, rid) if fetch_docs else None
    e = build_entry(rid, nr.iso_date(r["request_date"]), r.get("request_state"),
                    r.get("visibility"), nr.split_departments(r.get("department_names"), known),
                    r.get("request_text") or "", n_docs, today())
    e["_checked"] = fetch_docs
    return e


def fresh_from_detail(client, rid):
    try:
        d = client.request(rid)
    except nr.NotPublic:
        return {"_checked": True, "state": None, "visibility": "Not public", "published": False}
    deps = [x["name"] for x in d.get("departments") or []]
    e = build_entry(rid, nr.iso_date(d.get("request_date")), d.get("request_state"),
                    d.get("request_visibility") or d.get("visibility"), deps,
                    d.get("request_text") or "", count_public_docs(client, rid), today())
    e["_checked"] = True
    return e


def run(client, root, since=None, max_pages=None, dry_run=False, log=print):
    queue = load_queue(root)
    if since is None:
        since = max((e["request_date"] for e in queue.values()), default=BACKFILL_START)
    known = client.departments()
    now = today()
    seen, created, updated = set(), [], []

    page = 1
    while max_pages is None or page <= max_pages:
        d = client.list_page(page)
        reqs = d.get("requests") or []
        if not reqs:
            break
        for r in reqs:
            date = nr.iso_date(r["request_date"])
            if date < since or r["id"] in seen:
                continue
            seen.add(r["id"])
            if r["id"] in queue:
                e = queue[r["id"]]
                listed_state = r.get("request_state")
                need_docs = not settled(e) or listed_state != e.get("state")
                fresh = fresh_from_listing(client, r, known, need_docs)
                if refresh(e, fresh, now):
                    updated.append(e)
            else:
                e = fresh_from_listing(client, r, known, True)
                e.pop("_checked", None)
                queue[e["id"]] = e
                created.append(e)
        oldest = nr.iso_date(reqs[-1]["request_date"])
        log(f"page {page}: {len(reqs)} requests, oldest {oldest}")
        if oldest < since:
            break
        page += 1

    # Re-check unsettled entries the listing pass did not reach.
    for rid, e in sorted(queue.items()):
        if rid in seen or settled(e):
            continue
        fresh = fresh_from_detail(client, rid)
        if refresh(e, fresh, now):
            updated.append(e)

    for e in created + updated:
        e.pop("_checked", None)
        if not dry_run:
            save_entry(root, e)
    log(f"{len(created)} new, {len(updated)} updated"
        f"{' (dry run, nothing written)' if dry_run else ''}; since {since}")
    return created, updated


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", help="earliest request_date to scan (YYYY-MM-DD)")
    ap.add_argument("--max-pages", type=int, help="stop after this many listing pages (100 each)")
    ap.add_argument("--dry-run", action="store_true", help="fetch and report, write nothing")
    ap.add_argument("--cache-dir", help="cache portal responses here (development only; keep outside the repo)")
    ap.add_argument("--root", default=ROOT, help="archive root (default: this repository)")
    a = ap.parse_args(argv)
    if a.since and not re.match(r"^\d{4}-\d{2}-\d{2}$", a.since):
        ap.error("--since must be YYYY-MM-DD")
    run(nr.Client(cache_dir=a.cache_dir), a.root, a.since, a.max_pages, a.dry_run)


if __name__ == "__main__":
    main()
