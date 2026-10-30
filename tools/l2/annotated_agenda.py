#!/usr/bin/env python3
"""Build an L2 record of a Council annotated agenda from its L1 text.

    python3 tools/l2/annotated_agenda.py <recipe.json> [--check]
    python3 tools/l2/annotated_agenda.py --new <annotated agenda L1 text id> --contributor <login>

What the City Clerk printed, in structured form: attendance, and for each agenda item its number,
section and heading, the From, Contact and Action text as printed, who the From line names and in
what role, members the Action records as added as co-sponsors, the number of speakers, each motion
in the order taken with its mover, seconder, result and recorded vote, and the correspondence the
agenda lists. Every item carries the lines of the L1 text it was read from. Nothing is corrected
from any other record: a motion the Clerk did not label a substitute is not called one here.

Lines are lines of the L1 text, counted from 1. Members are named by their ids in
registry/speakers.json; a name the registry does not hold is kept as printed under "as_printed"
with "member": null.

What it parses, and how:
  - An item begins at a line starting with an item number and a period ("14.", "20b.", "1 a."),
    a letter in sequence ("A.", "B.") or after a Continued or Old Business heading, or
    "Urgent Item:" or "Urgency Item:" (recorded as urgent-1, urgent-2, ... in the order printed,
    since the Clerk prints no number), when a From: line follows within twelve lines before anything that cannot be
    part of a heading. Numbered sub-recommendations and correspondence entries have no From: line.
  - An item ends at the next item, or sooner at a Communications, Adjournment, or non-agenda
    public comment heading.
  - Motions are read from "M/S/C", "M/S/Failed" or "Motion/Seconded" marks, "On the severed
    portion", "Action:" statements and "Vote:" lines, in order. A vote belongs to the motion it
    follows and to no other; a motion with no vote line has "vote": null. A "First Reading Vote:"
    printed in a second-reading item's heading, before any Action, is the earlier meeting's vote
    and is kept apart as prior_first_reading_vote.
  - kind: procedural, amendment, substitute (only when printed so), main, competing (a failed main
    motion followed by the motion that decided the item), action (an Action with its own vote and
    no M/S mark), or unattributed (a vote line with no motion to attach to). The final motion is
    the last main, substitute or action motion.
  - In a vote, a name printed with "(recused ...)" under another heading is listed under recused,
    and normalization_notes says so. "All Ayes" is recorded as all_ayes, with no names: who was
    present is in attendance.
  - Correspondence: each numbered entry under Communications and Supplemental Communications,
    grouped under the Clerk's heading; count is 1, the "(n)" printed after it, or the N of "N
    similarly worded" or "N form" letters.
"""
import datetime, hashlib, json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LINE_BASIS = "lines of the L1 text, counted from 1"

PAGE_HEADER = re.compile(r"^\w+day,\s+\w+\s+\d{1,2},\s+\d{4}\b.*\bPage\s+\d+$|^ANNOTATED AGENDA\b.*Page\s+\d+$|^Page\s+\d+$")
SECTION = re.compile(
    r"^(Ceremonial Matters|Consent Calendar(?: [–-] Continued Business)?|Council Consent Items|"
    r"Action Calendar(?:\s*[–-]\s*[A-Za-z ]+)?|Council Action Items|Information Reports|Information Calendar|"
    r"Worksessions?|Presentations?|Public Hearings?|Appeals?|Continued Business|Old Business|New Business|"
    r"Preliminary Matters|Recess Items|Communications|Supplemental Communications and Reports(?: \d+)?|Adjournment)$")
TAIL = re.compile(r"^(Adjournment$|Public Comment\s*[–\-—]\s*Items Not Listed|Communications$|"
                  r"Supplemental Communications and Reports(?: \d+)?$)")
FIELD = re.compile(r"^(From|Recommendation|Financial Implications|Contact|Action):\s*(.*)$")
HEADING_STOP = re.compile(r"^(?:\d{1,3}[a-z]?\s*[.·]\s|(?:(?:[Ff]irst|[Ss]econd)\s+[Rr]eading\s+)?V\s?ote:|"
                          r"Action:|Recommendation:|Financial\s+Implications:|Contact:)")
FROM_WINDOW = 12

# votes and motions
VOTE_START = re.compile(r"(?m)^(?:(First|Second)\s+Reading\s+)?V\s?ote:", re.I)
VOTE_CLAUSE = re.compile(r"\b(Ayes?|Noes?|Abstain(?:ed|s)?|Absent|Recused)\s*[–\-—]", re.I)
ALL_AYES = re.compile(r"^\s*All\s+Ayes", re.I)
CLAUSE_KEY = {"aye": "ayes", "ayes": "ayes", "no": "noes", "noes": "noes", "abstain": "abstain",
              "abstained": "abstain", "abstains": "abstain", "absent": "absent", "recused": "recused"}
CLAUSE_LABEL = {"ayes": "Ayes", "noes": "Noes", "abstain": "Abstain", "absent": "Absent", "recused": "Recused"}
MOTION_MARK = re.compile(r"\bM\s*/\s*S\s*/\s*(C|Carried|Failed|F)\b|\bMotion\s*/\s*Seconded\b", re.I)
SEVERED_PART = re.compile(r"(?m)^On\s+the\s+severed\s+portion", re.I)
ACTION_START = re.compile(r"(?m)^Action:")
MOVERS = re.compile(r"^\s*\(([^()/]+)/([^()]+)\)")
PROCEDURAL = re.compile(r"suspend\s+the\s+rules|extend\s+the\s+meeting|(?:close|open|re-?open)\s+the\s+public\s+hearing"
                        r"|\badjourn|\brecess\b|\breconsider\b|\baccept\b[^.]{0,60}?\b(?:materials?|communications?)\b", re.I)
AMENDMENT = re.compile(r"\bamend(?:ment)?s?\s+(?:to\s+)?the\s+(?:main\s+|original\s+|substitute\s+)?motion\b"
                       r"|\bto\s+amend\s+the\s+(?:main\s+|original\s+|substitute\s+)?motion\b", re.I)
SUBSTITUTE = re.compile(r"\bsubstitute\s+motion\b", re.I)
SEVER = re.compile(r"\bsever", re.I)
DISPOSITION = {"c": "carried", "carried": "carried", "failed": "failed", "f": "failed"}
SPEAKERS = re.compile(r"\b(\d+)\s+speakers?\b", re.I)

# attendance
ROLL_CALL = re.compile(r"Roll\s+Call:\s*(\d+:\d+\s*[ap]\.m\.)", re.I)
PRESENT = re.compile(r"Present:\s*(.+?)(?=\nAbsent:|\nCouncilmember|\nLand\s+Acknowledge|\nCeremonial|\Z)", re.S | re.I)
ABSENT = re.compile(r"Absent:\s*(.+?)(?=\nCouncilmember\s+\S+\s+present|\nLand\s+Acknowledge|\nCeremonial|\nAction:|\Z)", re.S | re.I)
LATE = re.compile(r"(?:Councilmember|Mayor)\s+(\S+)\s+present\s+at\s+(\d+:\d+\s*[ap]\.m\.)", re.I)

# attribution
ROLE = re.compile(r"\(\s*(Co-?\s*Authors?|Authors?|Co-?\s*Sponsors?)\s*\)", re.I)
ADDED = re.compile(r"([^.]*?)\badded as (?:a )?co-\s?sponsors?", re.I)
ADD_PHRASE = re.compile(r"\badd(?:ing)?\s+((?:Councilmembers?|Mayor)\s[^.]*?)\s+as\s+(?:a\s+)?co-\s?sponsors?", re.I)
TITLE_WORDS = {"councilmember", "councilmembers", "mayor", "vice", "and"}

# correspondence
COMM_SECTION = re.compile(r"^(Communications|Supplemental Communications and Reports\s*\d*)$")
ENTRY = re.compile(r"^(\d{1,4})\.\s+(.*)$")
FORM = re.compile(r"([\d,]+)\s+(?:similarly[- ]worded|form)", re.I)
MULT = re.compile(r"\((\d+)\)\s*$")


def load_registry(root=ROOT):
    with open(os.path.join(root, "registry", "speakers.json")) as f:
        reg = json.load(f)
    keys = {}
    for o in reg["officeholders"]:
        surname = o["name"].split()[-1]
        keys[re.sub(r"[^a-z]", "", surname.lower())] = o["id"]
        keys[re.sub(r"[^a-z]", "", o["id"][4:])] = o["id"]
    firsts = {re.sub(r"[^a-z]", "", o["name"].split()[0].lower()) for o in reg["officeholders"]}
    return keys, firsts


class Parser:
    def __init__(self, root=ROOT):
        self.keys, self.firsts = load_registry(root)

    # names -------------------------------------------------------------
    def member(self, name):
        k = re.sub(r"[^a-z]", "", name.lower())
        return self.keys.get(k)

    def person(self, name):
        name = re.sub(r"\s+", " ", name).strip().strip(".").strip()
        m = self.member(name) or self.member(name.split()[-1] if name.split() else "")
        return {"member": m, "as_printed": name}

    def names(self, raw):
        """[(person, recused)] for one vote clause; other parentheticals are dropped."""
        marked = re.sub(r"\([^)]*\)", lambda m: " \x00R\x00 " if re.match(r"^\(\s*recus", m.group(0), re.I) else " ", raw)
        out = []
        for part in re.split(r"[,;\n]+|\band\b", marked):
            rec = "\x00R\x00" in part
            part = part.replace("\x00R\x00", " ").strip().strip(".").strip()
            if part and part.lower() != "none":
                out.append((self.person(part), rec))
        return out

    # votes -------------------------------------------------------------
    @staticmethod
    def span_end(text):
        depth = 0
        for i, ch in enumerate(text):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth = max(0, depth - 1)
            elif ch == "." and depth == 0 and (i + 1 == len(text) or text[i + 1].isspace()):
                return i + 1
        return len(text)

    def vote(self, body, prefix):
        printed = re.sub(r"\s+", " ", f"{prefix.strip()} {body}").strip()
        v = {"all_ayes": False, "ayes": [], "noes": [], "abstain": [], "absent": [], "recused": [], "as_printed": printed}
        if ALL_AYES.match(body):
            v["all_ayes"] = True
            return v
        depth, inside = 0, []
        for ch in body:
            depth = depth + 1 if ch == "(" else max(0, depth - 1) if ch == ")" else depth
            inside.append(depth > 0)
        clauses = [c for c in VOTE_CLAUSE.finditer(body) if not inside[c.start()]]
        if not clauses or CLAUSE_KEY[clauses[0].group(1).lower()] != "ayes":
            return None
        moved = {}
        for i, c in enumerate(clauses):
            end = clauses[i + 1].start() if i + 1 < len(clauses) else len(body)
            key = CLAUSE_KEY[c.group(1).lower()]
            for p, rec in self.names(body[c.end():end]):
                if rec and key != "recused":
                    v["recused"].append(p)
                    moved.setdefault(key, []).append(p["as_printed"])
                else:
                    v[key].append(p)
        if moved:
            v["normalization_notes"] = "; ".join(f"{', '.join(n)} printed under {CLAUSE_LABEL[k]} with '(recused ...)'; "
                                                 f"listed under recused" for k, n in moved.items())
        return v

    def motions(self, chunk):
        events = [(m.start(), 0, "action", m) for m in ACTION_START.finditer(chunk)]
        events += [(m.start(), 1, "motion", m) for m in MOTION_MARK.finditer(chunk)]
        events += [(m.start(), 1, "severed_part", m) for m in SEVERED_PART.finditer(chunk)]
        events += [(m.start(), 2, "vote", m) for m in VOTE_START.finditer(chunk)]
        events.sort(key=lambda e: (e[0], e[1]))
        out, prior, open_m, action_at = [], [], None, None

        def close(mo, end):
            if mo.get("_end") is None:
                mo["_end"] = end
        for idx, (pos, _, kind, m) in enumerate(events):
            if kind == "action":
                if open_m is not None:
                    close(open_m, pos)
                open_m, action_at = None, m.end()
            elif kind in ("motion", "severed_part"):
                if open_m is not None:
                    close(open_m, pos)
                if kind == "motion":
                    mv = MOVERS.match(chunk[m.end():])
                    open_m = {"kind": None, "movers": [self.person(mv.group(1)), self.person(mv.group(2))] if mv else None,
                              "disposition": DISPOSITION[m.group(1).lower()] if m.group(1) else "unknown",
                              "severed": bool(action_at is not None and SEVER.search(chunk[action_at:pos])),
                              "_start": (m.end() + mv.end()) if mv else m.end(), "_end": None, "vote": None, "vote_label": None}
                else:
                    open_m = {"kind": None, "movers": None, "disposition": "unknown", "severed": True,
                              "_start": m.start(), "_end": None, "vote": None, "vote_label": None}
                out.append(open_m)
                action_at = None
            else:
                rest = chunk[m.end():]
                body = rest[:self.span_end(rest)]
                nxt = events[idx + 1][0] if idx + 1 < len(events) else len(chunk)
                body = body[:max(0, nxt - m.end())]
                cut = re.search(r"\n(?:" + SECTION.pattern.strip("^$") + r")(?:\n|$)", body)
                if cut:   # a vote line with no closing period stops at a section heading
                    body = body[:cut.start()]
                v = self.vote(body, m.group(0))
                label = {"": "vote", "first": "first_reading", "second": "second_reading"}[(m.group(1) or "").lower()]
                if open_m is not None and open_m["vote"] is None:
                    close(open_m, pos)
                    open_m.update(vote=v, vote_label=label)
                elif label == "first_reading" and not out and not ACTION_START.search(chunk[:pos]):
                    prior.append(v)
                elif action_at is not None:
                    out.append({"kind": "action", "movers": None, "disposition": "unknown", "severed": False,
                                "_start": action_at, "_end": pos, "vote": v, "vote_label": label})
                else:
                    out.append({"kind": "unattributed", "movers": None, "disposition": "unknown", "severed": False,
                                "_start": pos, "_end": pos, "vote": v, "vote_label": label})
                open_m, action_at = None, None
        for i, mo in enumerate(out):
            end = mo.pop("_end")
            text = re.sub(r"\s+", " ", chunk[mo.pop("_start"):len(chunk) if end is None else end]).strip()
            mo["text"] = re.sub(r"^to:?\s+", "", text)
            mo["seq"] = i
            if mo["kind"] is None:
                t = mo["text"][:200]
                mo["kind"] = ("procedural" if PROCEDURAL.search(t) else "amendment" if AMENDMENT.search(t)
                              else "substitute" if SUBSTITUTE.search(t) else "main")
            if re.search(r"\bsevered\s+portion", mo["text"], re.I):
                mo["severed"] = True
        deciding = [mo for mo in out if mo["kind"] in ("main", "substitute", "action")]
        final = deciding[-1] if deciding else None
        for mo in out:
            mo["final"] = mo is final
            if (mo["kind"] == "main" and mo["disposition"] == "failed" and not mo["severed"]
                    and final is not None and mo is not final and mo["seq"] < final["seq"]):
                mo["kind"] = "competing"
        keys = ("seq", "kind", "movers", "disposition", "severed", "final", "text", "vote_label", "vote")
        return [{k: mo[k] for k in keys} for mo in out], (prior[0] if prior else None)

    # attribution -------------------------------------------------------
    def attribution(self, text):
        """[{member, as_printed, role, order}] from a From: line that names a Councilmember or the Mayor."""
        if not re.search(r"Councilmember|Mayor", text):
            return []
        out = []
        for part in re.split(r"\s*[,;]\s*|\s+and\s+", re.sub(r"\(Reviewed by[^)]*\)", "", text)):
            part = part.strip()
            if not part:
                continue
            rm = ROLE.search(part)
            role = None
            if rm:
                role = {"author": "author", "coauthor": "co-author", "cosponsor": "co-sponsor"}[
                    re.sub(r"[\s-]", "", rm.group(1).lower()).rstrip("s")]
            words = re.sub(r"\b(Councilmembers?|Mayor|Vice)\b", " ", re.sub(r"\(.*?\)", " ", part))
            hits = [self.keys[k] for k in (re.sub(r"[^a-z]", "", w.lower()) for w in words.split())
                    if k in self.keys and k not in self.firsts]
            if len(set(hits)) == 1:
                out.append({"member": hits[0], "as_printed": part, "role": role, "order": len(out) + 1})
            elif not hits and re.search(r"Councilmember|Mayor", part):
                out.append({"member": None, "as_printed": part, "role": role, "order": len(out) + 1})
            elif hits:
                raise SystemExit(f"several members in one fragment of a From line: {part!r}")
        return out

    def name_run(self, clause):
        found = []
        for w in reversed(re.findall(r"[A-Za-z’']+|,", clause)):
            k = re.sub(r"[^a-z]", "", w.lower())
            if w == "," or k in TITLE_WORDS:
                continue
            if k in self.keys:
                found.append(self.keys[k])
                continue
            break
        return list(reversed(found))

    def added(self, action):
        out, seen = [], set()
        for rx in (ADDED, ADD_PHRASE):
            for m in rx.finditer(action):
                for mid in self.name_run(m.group(1)):
                    if mid not in seen:
                        seen.add(mid)
                        out.append({"member": mid, "as_printed": re.sub(r"\s+", " ", m.group(0)).strip()})
        return out


def lines_of(text):
    """[(line number, text without leading or trailing space)] with page headers and form feeds dropped."""
    out = []
    for n, raw in enumerate(text.split("\n"), 1):
        s = raw.replace("\f", "").strip()
        if PAGE_HEADER.match(s):
            s = ""
        out.append((n, s))
    return out


def item_starts(lines):
    starts, letter = [], "A"
    for i, (n, s) in enumerate(lines):
        m = re.match(r"^(\d{1,3})( ?[a-z])?\s*[.·]\s+(?=\S)", s)
        urgent = re.match(r"^Urgen(?:t|cy) Item:\s*\S", s)
        if not m and not urgent:
            m = re.match(r"^([A-Z])()\s?\.\s+(?=\S)", s)
            prev = next((x for _, x in reversed(lines[:i]) if x), "")
            after = re.search(r"(Continued|Old)\s+Business$", prev)
            if not m or (m.group(1) != letter and not after):
                continue
        window = [x for _, x in lines[i + 1:i + 1 + FROM_WINDOW]]
        ok = False
        for w in window:
            if w.startswith("From:"):
                ok = True
                break
            if HEADING_STOP.match(w):
                break
        if not ok:
            continue
        if urgent:
            starts.append((i, f"urgent-{sum(1 for _, x in starts if x.startswith('urgent-')) + 1}"))
            continue
        if m.group(1).isalpha():
            letter = chr(ord(m.group(1)) + 1)
        starts.append((i, m.group(1) + (m.group(2) or "").strip()))
    return starts


def fields(block):
    """From, Contact, Financial Implications, Recommendation (first paragraph), Action (to the item's end)."""
    out, key = {}, "heading"
    for _, s in block[1:]:
        f = FIELD.match(s)
        if f and f.group(1) not in out:
            key = f.group(1)
            out[key] = [f.group(2)]
            continue
        if key == "heading" or not s:
            if not s and key in ("From", "Contact", "Financial Implications", "Recommendation"):
                key = None
            continue
        if key:
            out[key].append(s)
    return {k: re.sub(r"\s+", " ", " ".join(v)).strip() for k, v in out.items()}


def communications(lines):
    start = next((i for i, (_, s) in enumerate(lines) if s == "Communications"), None)
    if start is None:
        start = next((i for i, (_, s) in enumerate(lines) if COMM_SECTION.match(s)), None)
    if start is None:
        return []
    groups, cur, section, prev_entry = [], None, "Communications", False
    for n, s in lines[start:]:
        if not s or re.match(r"^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday),", s):
            prev_entry = False if not s else prev_entry
            continue
        if prev_entry and cur and cur["entries"] and not ENTRY.match(s) and not COMM_SECTION.match(s) \
                and not s.startswith("Item #"):
            cur["entries"][-1]["as_printed"] += " " + s
            continue
        m = COMM_SECTION.match(s)
        if m:
            section, cur, prev_entry = m.group(1), None, False
            continue
        e = ENTRY.match(s)
        if e:
            if cur is None:
                cur = {"section": section, "heading": None, "line": n, "entries": []}
                groups.append(cur)
            cur["entries"].append({"as_printed": e.group(2), "line": n})
            prev_entry = True
            continue
        if cur is not None and not cur["entries"] and (cur["heading"] or "").startswith("Item #"):
            cur["heading"] += (" / " if s.startswith("Item #") else " ") + s
            continue
        cur = {"section": section, "heading": s, "line": n, "entries": []}
        groups.append(cur)
    out = []
    for g in groups:
        if not g["entries"]:
            continue
        for x in g["entries"]:
            f, mm = FORM.search(x["as_printed"]), MULT.search(x["as_printed"])
            x["count"], x["count_basis"] = ((int(f.group(1).replace(",", "")), "similarly worded or form letters") if f
                                            else (int(mm.group(1)), "parenthesized number") if mm else (1, "one entry"))
        g["items"] = re.findall(r"Item #\s*([0-9]+[a-zA-Z]?)", g["heading"] or "")
        out.append(g)
    return out


def attendance(text):
    a = {"roll_call_time": None, "present": [], "absent": [], "arrived_late": []}
    m = ROLL_CALL.search(text)
    if m:
        a["roll_call_time"] = m.group(1).strip()
    return a


def parse(text, root=ROOT):
    """The record for one annotated agenda's L1 text (str)."""
    P = Parser(root)
    lines = lines_of(text)
    flat = "\n".join(s for _, s in lines)
    att = attendance(flat)
    for rx, key in ((PRESENT, "present"), (ABSENT, "absent")):
        m = rx.search(flat)
        if m:
            raw = re.split(r"Land\s+Acknowledge|Ceremonial|Action:", m.group(1).replace("\n", " "), flags=re.I)[0].strip()
            if raw.lower().strip(".") != "none":
                att[key] = [P.person(x) for x in re.split(r"[,;]+|\band\b", raw) if x.strip().strip(".")]
    att["arrived_late"] = [{**P.person(m.group(1)), "time": m.group(2).strip()} for m in LATE.finditer(flat)]
    starts = item_starts(lines)
    tails = [i for i, (_, s) in enumerate(lines) if TAIL.match(s)]
    section_at, section = {}, None
    for i, (_, s) in enumerate(lines):
        if SECTION.match(s):
            section = s
        section_at[i] = section
    items, seen = [], set()
    for k, (i, number) in enumerate(starts):
        # an item ends at the next item, or at a heading that closes the item sections, if sooner
        end = min([starts[k + 1][0] if k + 1 < len(starts) else len(lines)] + [t for t in tails if t > i])
        block = lines[i:end]
        while len(block) > 1 and (not block[-1][1] or SECTION.match(block[-1][1])):
            block.pop()   # the next section's heading is not part of the item
        key = number.lower()
        if key in seen:
            raise SystemExit(f"line {lines[i][0]}: item {number} begins twice")
        seen.add(key)
        head = re.sub(r"^(?:\d{1,3}(?: ?[a-z])?|[A-Z])\s*[.·]\s*|^Urgen(?:t|cy) Item:\s*", "", block[0][1])
        heading = [head]
        for _, s in block[1:]:
            if not s or s.startswith("From:") or HEADING_STOP.match(s):
                break
            heading.append(s)
        f = fields(block)
        chunk = "\n".join(s for _, s in block)
        a = chunk.find("\nAction:")
        action = re.sub(r"\s+", " ", chunk[a + 1:]).strip() if a >= 0 else ""
        motions, prior = P.motions(chunk)
        sp = SPEAKERS.search(chunk)
        rec = {"item": number, "section": section_at[i], "heading": re.sub(r"\s+", " ", " ".join(heading)).strip(),
               "lines": [block[0][0], block[-1][0]], "from": f.get("From"), "contact": f.get("Contact"),
               "financial_implications": f.get("Financial Implications"), "action": action or None,
               "speakers": int(sp.group(1)) if sp else None,
               "attribution": P.attribution(f.get("From") or ""), "added_cosponsors": P.added(action),
               "motions": motions, "prior_first_reading_vote": prior}
        listed = {x["member"] for x in rec["attribution"]}
        rec["added_cosponsors"] = [x for x in rec["added_cosponsors"] if x["member"] not in listed]
        items.append(rec)
    if not items and not att["roll_call_time"]:
        raise SystemExit("no agenda items and no roll call found")
    return {"attendance": att, "items": items, "communications": communications(lines)}


def load(root, mid):
    with open(os.path.join(root, "manifest", mid + ".json")) as f:
        e = json.load(f)
    with open(os.path.join(root, e["file"]), "rb") as f:
        raw = f.read()
    if hashlib.sha256(raw).hexdigest() != e["sha256"]:
        raise SystemExit(f"{mid}: file does not match its manifest sha256")
    return e, raw


def build(recipe, root=ROOT):
    t, raw = load(root, recipe["text"])
    doc = {"id": recipe["id"], "annotated_agenda": t["derived_from"]["id"], "text": recipe["text"],
           "text_sha256": t["sha256"], "line_basis": LINE_BASIS, **parse(raw.decode("utf-8"), root)}
    return (json.dumps(doc, indent=1, ensure_ascii=False) + "\n").encode("utf-8"), len(doc["items"])


def write_new(text_id, contributor, root=ROOT, today=None):
    """Recipe, record and manifest entry for an annotated agenda whose L1 text is archived."""
    today = today or datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    t, _ = load(root, text_id)
    aa_id = t["derived_from"]["id"]
    with open(os.path.join(root, "manifest", aa_id + ".json")) as f:
        aa = json.load(f)
    if aa.get("doc_type") != "annotated_agenda":
        raise SystemExit(f"{aa_id} is a {aa.get('doc_type')}, not an annotated_agenda")
    lid = aa_id + "-record"
    rel, recipe_rel = f"L2/annotated-agendas/{lid}.json", f"tools/l2/recipes/{lid}.json"
    recipe = {"id": lid, "file": rel, "text": text_id}
    data, n = build(recipe, root)
    existing = os.path.join(root, "manifest", lid + ".json")
    if os.path.exists(existing):
        with open(existing) as f:
            if json.load(f).get("sha256") == hashlib.sha256(data).hexdigest():
                return lid, n
        raise SystemExit(f"{lid} is already archived with a different record; an archived record is not replaced")
    for path, body in ((recipe_rel, (json.dumps(recipe, indent=1) + "\n").encode()), (rel, data)):
        os.makedirs(os.path.dirname(os.path.join(root, path)), exist_ok=True)
        with open(os.path.join(root, path), "wb") as f:
            f.write(body)
    entry = {"id": lid, "layer": "L2", "doc_type": "annotated_agenda_record", "body": aa.get("body"),
             "meeting_date": aa.get("meeting_date"), "meeting_label": aa.get("meeting_label"),
             "title": f"Structured record: {aa['title']}", "file": rel,
             "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
             "derived_from": [{"id": text_id, "sha256": t["sha256"]}],
             "extraction": {"tool": "tools/l2/annotated_agenda.py",
                            "command": f"python3 tools/l2/annotated_agenda.py {recipe_rel}", "recipe": recipe_rel},
             "notices": [], "contributed_by": contributor, "archived": today}
    with open(existing, "w") as f:
        json.dump(entry, f, indent=1, ensure_ascii=False)
        f.write("\n")
    return lid, n


def main(argv):
    if len(argv) >= 3 and argv[1] == "--new":
        if "--contributor" not in argv:
            raise SystemExit("--new needs --contributor <GitHub login>")
        lid, n = write_new(argv[2], argv[argv.index("--contributor") + 1])
        print(f"{lid}: {n} items; recipe, record and manifest entry written")
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
    print(f"{recipe['id']}: {n} items, sha256 {hashlib.sha256(data).hexdigest()} -> {recipe['file']}")


if __name__ == "__main__":
    main(sys.argv)
