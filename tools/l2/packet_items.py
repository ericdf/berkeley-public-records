#!/usr/bin/env python3
"""Build an L2 item index for a Council agenda packet from its L1 text.

    python3 tools/l2/packet_items.py <recipe.json> [--check]
    python3 tools/l2/packet_items.py --new <packet L1 text id> --contributor <login>

An agenda packet is one PDF holding the agenda and every item's material. Splitting it would make
files the City never published, so item-level addressing comes from this index instead: for each
agenda item, its number and heading as printed, the agenda section it is listed under, and the
range of packet pages holding its material. Citations take the form <packet id>, pp. 212-240.

Pages are the form-feed-delimited pages of the L1 text (pdftotext -layout writes one form feed
after every page), counted from 1, which are the PDF's pages.

What it parses, and refuses when the packet does not fit:
  - Agenda items are read from the pages before the first item's material, up to the agenda's
    "Communications" heading: lines that begin with an item number and a period ("20b.  Title").
    A repeated item number is refused.
  - An item's material begins on a page whose first line is "Page 1 of N" and whose next line is
    the item number (the City's item tab). It runs N pages. A number not on the agenda, material
    that overlaps the next item's, or material running past the end of the packet is refused.
  - Every page is accounted for: the agenda, each item's range, and "other" spans (pages after
    the last item, such as the communications notice).
An agenda item with no material in the packet has "pages": null.

The recipe (tools/l2/recipes/<id>.json) names the L2 id, the output path, and the packet's L1
text id. --check rebuilds and compares with the archived file. --new writes the recipe, the
index and its manifest entry for a packet whose L1 text is already archived; it never replaces
an archived index (an identical rerun changes nothing, a different one is refused).
"""
import datetime, hashlib, json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ITEM_LINE = re.compile(r"^\s{0,4}(\d{1,3}[a-z]?)\.\s+(\S.*?)\s*$")
TAB_FIRST = re.compile(r"^Page 1 of (\d+)$")
TAB_ITEM = re.compile(r"^(\d{1,3}[a-z]?)$")
SECTION = re.compile(r"^(Ceremonial Matters|Consent Calendar|Action Calendar(\s*[–-]\s*\S.*)?|Information Reports|"
                     r"Information Calendar|Public Hearings?|Appeals?|Presentations?|Worksession|"
                     r"Council Action Items|Recess Items|Old Business|New Business)$")
HEADING_END = re.compile(r"^(From:|Recommendation:|Financial Implications:|Contact:)")
COMMUNICATIONS = "Communications"


def norm(token):
    """'01' and '1' are the same item; '20B' and '20b' too."""
    m = re.match(r"^0*(\d+)([a-z]?)$", token.lower())
    return f"{int(m.group(1))}{m.group(2)}" if m else token.lower()


def nonblank(page):
    return [l.strip() for l in page.splitlines() if l.strip()]


def load(root, mid):
    with open(os.path.join(root, "manifest", mid + ".json")) as f:
        e = json.load(f)
    with open(os.path.join(root, e["file"]), "rb") as f:
        raw = f.read()
    if hashlib.sha256(raw).hexdigest() != e["sha256"]:
        raise SystemExit(f"{mid}: file does not match its manifest sha256")
    return e, raw


def agenda_items(pages):
    """[(item, section, heading)] from the agenda pages, in order."""
    items, section, seen = [], None, set()
    lines = [l for p in pages for l in p.splitlines()]
    k = 0
    while k < len(lines):
        s = lines[k].strip()
        if s == COMMUNICATIONS:
            break
        if SECTION.match(s):
            section = s
        m = ITEM_LINE.match(lines[k])
        if m:
            item = norm(m.group(1))
            if item in seen:
                raise SystemExit(f"agenda lists item {item} twice before the Communications heading")
            seen.add(item)
            heading = [m.group(2)]
            j = k + 1
            while j < len(lines) and len(heading) < 4:
                t = lines[j].strip()
                if not t or HEADING_END.match(t) or ITEM_LINE.match(lines[j]) or SECTION.match(t):
                    break
                heading.append(t)
                j += 1
            items.append((item, section, " ".join(heading)))
        k += 1
    return items


def index(text):
    """The index for one packet's L1 text (bytes). Returns a dict."""
    pages = text.decode("utf-8").split("\f")
    if pages and not pages[-1].strip():
        pages = pages[:-1]
    n = len(pages)
    tabs = []
    for i, p in enumerate(pages, 1):
        lines = nonblank(p)
        if len(lines) >= 2 and TAB_FIRST.match(lines[0]) and TAB_ITEM.match(lines[1]):
            tabs.append((i, norm(lines[1]), int(TAB_FIRST.match(lines[0]).group(1))))
    if not tabs:
        raise SystemExit("no item material found (no page begins 'Page 1 of N' followed by an item number)")
    agenda_end = tabs[0][0] - 1
    listed = agenda_items(pages[:agenda_end])
    if not listed:
        raise SystemExit(f"no agenda items found on pages 1-{agenda_end}")
    numbers = [i for i, _, _ in listed]
    ranges = {}
    for k, (start, item, length) in enumerate(tabs):
        if item not in numbers:
            raise SystemExit(f"page {start}: material for item {item}, which the agenda does not list")
        if item in ranges:
            raise SystemExit(f"page {start}: second start of material for item {item}")
        end = start + length - 1
        nxt = tabs[k + 1][0] if k + 1 < len(tabs) else n + 1
        if end >= nxt:
            raise SystemExit(f"item {item}: 'Page 1 of {length}' on page {start} runs to page {end}, "
                             f"past the next item's material on page {nxt}" if nxt <= n else
                             f"item {item}: 'Page 1 of {length}' on page {start} runs past the packet's {n} pages")
        ranges[item] = [start, end]
    spans, covered = [{"role": "agenda", "pages": [1, agenda_end]}], agenda_end
    for start, item, _ in tabs:
        if start > covered + 1:
            spans.append({"role": "other", "pages": [covered + 1, start - 1]})
        spans.append({"role": "item", "item": item, "pages": ranges[item]})
        covered = ranges[item][1]
    if covered < n:
        spans.append({"role": "other", "pages": [covered + 1, n]})
    items = [{"item": i, "section": s, "heading": h, "pages": ranges.get(i)} for i, s, h in listed]
    return {"pages": n, "items": items, "spans": spans}


def build(recipe, root=ROOT):
    t, raw = load(root, recipe["text"])
    src = (t.get("derived_from") or {}).get("id")
    if not src:
        raise SystemExit(f"{recipe['text']}: L1 text has no derived_from packet")
    doc = {"id": recipe["id"], "packet": src, "text": recipe["text"], "text_sha256": t["sha256"],
           "page_basis": "pages are the form-feed-delimited pages of the L1 text, counted from 1",
           **index(raw)}
    data = (json.dumps(doc, indent=1, ensure_ascii=False) + "\n").encode("utf-8")
    return data, len(doc["items"])


def write_new(text_id, contributor, root=ROOT, today=None):
    """Recipe, index and manifest entry for a packet whose L1 text is archived."""
    today = today or datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    t, _ = load(root, text_id)
    packet_id = t["derived_from"]["id"]
    with open(os.path.join(root, "manifest", packet_id + ".json")) as f:
        packet = json.load(f)
    if packet.get("doc_type") != "agenda_packet":
        raise SystemExit(f"{packet_id} is a {packet.get('doc_type')}, not an agenda_packet")
    lid = packet_id + "-items"
    rel = f"L2/agenda-packets/{lid}.json"
    recipe_rel = f"tools/l2/recipes/{lid}.json"
    recipe = {"id": lid, "file": rel, "text": text_id}
    data, n = build(recipe, root)
    existing = os.path.join(root, "manifest", lid + ".json")
    if os.path.exists(existing):
        with open(existing) as f:
            old = json.load(f)
        if old.get("sha256") == hashlib.sha256(data).hexdigest():
            return lid, n
        raise SystemExit(f"{lid} is already archived with a different index; an archived record is not "
                         f"replaced. Rebuild with --check, or add a new index under a new id")
    for path in (rel, recipe_rel):
        if os.path.exists(os.path.join(root, path)):
            raise SystemExit(f"{path} exists without a manifest entry for {lid}; remove it or resolve by hand")
    for path, body in ((recipe_rel, (json.dumps(recipe, indent=1) + "\n").encode()), (rel, data)):
        os.makedirs(os.path.dirname(os.path.join(root, path)), exist_ok=True)
        with open(os.path.join(root, path), "wb") as f:
            f.write(body)
    entry = {"id": lid, "layer": "L2", "doc_type": "agenda_packet_items", "body": packet.get("body"),
             "meeting_date": packet.get("meeting_date"), "meeting_label": packet.get("meeting_label"),
             "title": f"Item index: {packet['title']}", "file": rel,
             "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
             "derived_from": [{"id": text_id, "sha256": t["sha256"]}],
             "extraction": {"tool": "tools/l2/packet_items.py",
                            "command": f"python3 tools/l2/packet_items.py {recipe_rel}",
                            "recipe": recipe_rel},
             "notices": [], "contributed_by": contributor, "archived": today}
    with open(os.path.join(root, "manifest", lid + ".json"), "w") as f:
        json.dump(entry, f, indent=1, ensure_ascii=False)
        f.write("\n")
    return lid, n


def main(argv):
    if len(argv) >= 3 and argv[1] == "--new":
        if "--contributor" not in argv:
            raise SystemExit("--new needs --contributor <GitHub login>")
        lid, n = write_new(argv[2], argv[argv.index("--contributor") + 1])
        print(f"{lid}: {n} agenda items indexed; recipe, index and manifest entry written")
        return
    with open(argv[1]) as f:
        recipe = json.load(f)
    data, n = build(recipe)
    dest = os.path.join(ROOT, recipe["file"])
    if "--check" in argv:
        with open(dest, "rb") as f:
            same = f.read() == data
        print(f"{recipe['id']}: {'reproduces' if same else 'DOES NOT reproduce'} ({n} items)")
        sys.exit(0 if same else 1)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(data)
    print(f"{recipe['id']}: {n} items, sha256 {hashlib.sha256(data).hexdigest()}, {len(data)} bytes -> {recipe['file']}")


if __name__ == "__main__":
    main(sys.argv)
