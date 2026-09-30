#!/usr/bin/env python3
"""Print the weekly review list of undecided public-records candidates as Markdown.

  python3 tools/queue_report.py [--include-watching] [--root DIR]

Lists queue entries with status "candidate" that have no decision, plus entries decided
"watching" that have re-surfaced. Grouped by first department, sorted by rank_hints.score
(a sorting aid only) and then by date. Paste the output into the weekly review PR body.

Standard library only.
"""
import argparse, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nextrequest_watch import ROOT, load_queue  # noqa: E402


def pending(e, include_watching=False):
    dec = e.get("decision")
    if e.get("status") == "candidate":
        return dec is None or dec.get("status") == "watching"
    return include_watching and e.get("status") == "watching" and dec is None


def report(queue, include_watching=False):
    items = [e for e in queue.values() if pending(e, include_watching)]
    groups = {}
    for e in items:
        groups.setdefault((e.get("departments") or ["(no department)"])[0], []).append(e)
    key = lambda e: (-(e.get("rank_hints") or {}).get("score", 0), e["request_date"], e["id"])
    for g in groups.values():
        g.sort(key=key, reverse=False)
    order = sorted(groups, key=lambda d: (key(groups[d][0]), d))
    lines = [f"## Public-records review: {len(items)} undecided", ""]
    for dep in order:
        lines += [f"### {dep} ({len(groups[dep])})", ""]
        for e in groups[dep]:
            docs = e.get("document_count")
            docs = "?" if docs is None else docs
            flag = " — re-surfaced" if e.get("decision") else ""
            state = "" if e.get("state") == "Closed" else f", {e.get('state')}"
            lines.append(f"- [ ] **[{e['id']}]({e['url']})** · {e['request_date']} · "
                         f"{docs} public docs{state}{flag}")
            lines.append(f"  > {e.get('request_text_excerpt') or ''}")
        lines.append("")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--include-watching", action="store_true",
                    help="also list undecided requests that are still open or unpublished")
    ap.add_argument("--root", default=ROOT)
    a = ap.parse_args(argv)
    print(report(load_queue(a.root), a.include_watching))


if __name__ == "__main__":
    main()
