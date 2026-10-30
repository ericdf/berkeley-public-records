"""L3 transcript structure: the rules a structure file must meet (README, "L3 transcript structure").

A structure file divides one caption transcript into contiguous segments, each an agenda item or a
procedural block, with the item's public-comment span where it has one. These checks are mechanical:
the file is pinned to its L1 text, the segments are in order and do not overlap, every anchor points
into the text, and each segment's opening quotation is found in the text at that segment.
Standard library only.
"""
import re

NOTICE = ("Item boundaries in this layer are inferred, not authoritative. They were read from the "
          "transcript by software and volunteers, not by the City. The annotated agenda is the official "
          "record of what the Council took up and decided; verify a boundary against the meeting video "
          "before relying on it.")
KINDS = {"opening", "ceremonial", "consent", "item", "public_comment_nonagenda", "council_comments",
         "recess", "other_procedural", "closing"}
LABELS = {"regular", "special", "jpfa", "sabra", "committee"}
LEVELS = {"verified", "cued", "inferred"}
HMS = re.compile(r"^\d{2}:\d{2}:\d{2}$")
CUE = re.compile(r"^(\d{2}):(\d{2}):(\d{2})\.\d{3} --> ")
CITY_NOTICE = re.compile(r"^(This information provided by |cannot certify the following text|following text since we did)")
SPEAKER = re.compile(r"^(>>\s*)?[^:\n]{1,40}:\s")
LABEL_IN_QUOTE = re.compile(r"(>>\s*)?\b[A-Z][A-Za-z.' ]{0,38}:\s")
MEETING = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\S+)$")
WINDOW_BEFORE = 30   # seconds, or lines for a court-reporter transcript


def secs(hms):
    h, m, s = (int(x) for x in hms.split(":"))
    return h * 3600 + m * 60 + s


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def found(quote, text):
    """True when the quotation's words occur in text in order. An ellipsis in the quotation marks
    words left out; the parts on either side must each occur, in order."""
    pos = 0
    quote = LABEL_IN_QUOTE.sub(" ", quote)   # speaker labels are not compared; the text drops them too
    for part in re.split(r"…|\.\.\.", quote):
        q = norm(part)
        if not q:
            continue
        i = text.find(q, pos)
        if i < 0:
            return False
        pos = i + len(q)
    return True


def _clean(line):
    if CITY_NOTICE.match(line.strip()):
        return ""
    return SPEAKER.sub("", line)


def parse_text(text):
    """('vtt', [(second, text)...]) for Zoom captions (a WEBVTT line near the top, after the City's
    cover page if any), ('cart', [line...]) otherwise. Lines are numbered from 1, as an editor shows them."""
    lines = text.split("\n")
    if any(ln.strip() == "WEBVTT" for ln in lines[:60]):
        cues, cur = [], None
        for ln in lines:
            m = CUE.match(ln)
            if m:
                cur = [int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)), []]
                cues.append(cur)
            elif cur is not None and ln.strip() and not ln.strip().isdigit():
                cur[1].append(_clean(ln))
        return "vtt", [(s, " ".join(t)) for s, t in cues]
    return "cart", lines


def _span(seg, fmt, where, errs):
    if fmt == "vtt":
        if not (HMS.match(str(seg.get("start", ""))) and HMS.match(str(seg.get("end", "")))):
            errs.append(f"{where}: a timestamped transcript's segment needs start and end as HH:MM:SS")
            return None
        return secs(seg["start"]), secs(seg["end"])
    a, b = seg.get("start_line"), seg.get("end_line")
    if not (isinstance(a, int) and isinstance(b, int)):
        errs.append(f"{where}: a transcript without timestamps needs start_line and end_line")
        return None
    return a, b


def _window(fmt, body, a, b):
    if fmt == "vtt":
        return norm(" ".join(t for s, t in body if a - WINDOW_BEFORE <= s <= b))
    return norm(" ".join(_clean(x) for x in body[max(0, a - 1 - WINDOW_BEFORE):b]))


def check(doc, l1_text):
    """Problems with one structure file read against its L1 text, as a list of messages."""
    errs = []
    if doc.get("notice") != NOTICE:
        errs.append("a structure file must carry the standard notice verbatim")
    if not doc.get("producer"):
        errs.append("producer is required")
    fmt, body = parse_text(l1_text)
    limit = (body[-1][0] if body else 0) if fmt == "vtt" else len(body)
    segs = doc.get("segments") or []
    if not segs:
        errs.append("no segments")
    prev = None
    for k, s in enumerate(segs):
        where = f"segment {k}"
        sp = _span(s, fmt, where, errs)
        if s.get("kind") not in KINDS:
            errs.append(f"{where}: kind must be one of {sorted(KINDS)}")
        m = MEETING.match(str(s.get("meeting", "")))
        if not m or m.group(2) not in LABELS:
            errs.append(f"{where}: meeting must be '<YYYY-MM-DD> <label>', label one of {sorted(LABELS)}")
        if "item" not in s or not (s["item"] is None or isinstance(s["item"], str)):
            errs.append(f"{where}: item is the item as printed, or null")
        if s.get("level") not in LEVELS:
            errs.append(f"{where}: level must be one of {sorted(LEVELS)}")
        if s.get("level") == "verified":
            for f in ("video_timestamp", "verified_by", "verified_on"):
                if not s.get(f):
                    errs.append(f"{where}: a verified segment needs {f}")
        if sp is None:
            continue
        a, b = sp
        if a > b or b > limit + (1 if fmt == "cart" else 60):
            errs.append(f"{where}: span {a}–{b} is out of order or past the end of the text")
            continue
        if prev is not None and a < prev:
            errs.append(f"{where}: begins before the previous segment ends")
        prev = b
        if not s.get("evidence_start") or not found(s["evidence_start"], _window(fmt, body, a, b)):
            errs.append(f"{where}: evidence_start is not found in the text at this segment")
        for pc in s.get("public_comment") or []:
            psp = _span(pc, fmt, f"{where} public_comment", errs)
            if psp and not (a <= psp[0] <= psp[1] <= b):
                errs.append(f"{where}: public_comment span lies outside its segment")
    return errs
