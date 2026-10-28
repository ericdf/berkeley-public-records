"""tools/l2/packet_items.py: the agenda packet item index."""
import glob, hashlib, json, os, shutil, sys, tempfile, unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools", "l2"))
import packet_items as pk  # noqa: E402


def read_json(path):
    with open(path) as f:
        return json.load(f)


def write(path, data):
    with open(path, "wb" if isinstance(data, bytes) else "w") as f:
        f.write(data)

AGENDA = """AGENDA
BERKELEY CITY COUNCIL MEETING
Consent Calendar

1.        Amendments to the Zoning Ordinance
          (Second Reading)
          From: City Manager
          Recommendation: Adopt second reading.
          1. Adopt first reading of a related ordinance.

2a.   Report on Sidewalks
      From: Commission on Disability
\f
Consent Calendar
2b.   Companion Report: Report on Sidewalks
      From: City Manager

Action Calendar – New Business

3.    Developer Selection
      From: City Manager

Communications
 1. A Resident
"""


def packet(*tail):
    return (AGENDA + "\f" + "\f".join(tail) + "\f").encode()


ITEMS = ["Page 1 of 2\n01\nORDINANCE NO. 1\n", "Page 2 of 2\ntext\n", "\n",
         "Page 1 of 1\n2a\nCommission on Disability\n",
         "Page 1 of 2\n03\nOffice of the City Manager\n", "Page 2 of 2\n2\nnot a tab: a page two\n",
         "Communications\nnotice\n"]


class Index(unittest.TestCase):
    def test_items_ranges_and_spans(self):
        d = pk.index(packet(*ITEMS))
        self.assertEqual(d["pages"], 9)
        by = {i["item"]: i for i in d["items"]}
        self.assertEqual(list(by), ["1", "2a", "2b", "3"])          # nested "1." under item 1 is not an item
        self.assertEqual(by["1"]["heading"], "Amendments to the Zoning Ordinance (Second Reading)")
        self.assertEqual(by["1"]["pages"], [3, 4])
        self.assertEqual(by["2a"]["pages"], [6, 6])
        self.assertIsNone(by["2b"]["pages"])                       # no material in the packet
        self.assertEqual(by["3"]["section"], "Action Calendar – New Business")
        self.assertEqual(by["3"]["pages"], [7, 8])
        self.assertEqual(d["spans"], [
            {"role": "agenda", "pages": [1, 2]}, {"role": "item", "item": "1", "pages": [3, 4]},
            {"role": "other", "pages": [5, 5]}, {"role": "item", "item": "2a", "pages": [6, 6]},
            {"role": "item", "item": "3", "pages": [7, 8]}, {"role": "other", "pages": [9, 9]}])

    def test_refusals(self):
        with self.assertRaisesRegex(SystemExit, "does not list"):
            pk.index(packet("Page 1 of 1\n09\nx\n"))
        with self.assertRaisesRegex(SystemExit, "past the next item"):
            pk.index(packet("Page 1 of 3\n01\nx\n", "y\n", "Page 1 of 1\n2a\nz\n"))
        with self.assertRaisesRegex(SystemExit, "runs past the packet"):
            pk.index(packet("Page 1 of 5\n01\nx\n"))
        with self.assertRaisesRegex(SystemExit, "no item material"):
            pk.index(packet("nothing\n"))
        with self.assertRaisesRegex(SystemExit, "twice"):
            pk.index((AGENDA.replace("3.    Developer", "1.    Developer") + "\fPage 1 of 1\n01\nx\n\f").encode())


class NewAndCheck(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        os.makedirs(os.path.join(self.root, "manifest"))
        os.makedirs(os.path.join(self.root, "L1", "agenda-packets"))
        raw = packet(*ITEMS)
        rel = "L1/agenda-packets/bcc-2026-10-27-regular-agenda-packet-text.txt"
        write(os.path.join(self.root, rel), raw)
        for e in ({"id": "bcc-2026-10-27-regular-agenda-packet", "layer": "L0", "doc_type": "agenda_packet",
                   "body": "Berkeley City Council", "meeting_date": "2026-10-27", "meeting_label": "regular",
                   "title": "Agenda Packet", "file": None, "sha256": "a" * 64},
                  {"id": "bcc-2026-10-27-regular-agenda-packet-text", "layer": "L1", "file": rel,
                   "sha256": hashlib.sha256(raw).hexdigest(),
                   "derived_from": {"id": "bcc-2026-10-27-regular-agenda-packet", "sha256": "a" * 64}}):
            write(os.path.join(self.root, "manifest", e["id"] + ".json"), json.dumps(e))

    def test_new_writes_reproducible_index(self):
        lid, n = pk.write_new("bcc-2026-10-27-regular-agenda-packet-text", "ericdf", self.root, "2026-10-28")
        self.assertEqual((lid, n), ("bcc-2026-10-27-regular-agenda-packet-items", 4))
        e = read_json(os.path.join(self.root, "manifest", lid + ".json"))
        self.assertEqual((e["layer"], e["doc_type"], e["meeting_label"]), ("L2", "agenda_packet_items", "regular"))
        recipe = read_json(os.path.join(self.root, e["extraction"]["recipe"]))
        data, _ = pk.build(recipe, self.root)
        self.assertEqual(hashlib.sha256(data).hexdigest(), e["sha256"])
        self.assertEqual(json.loads(data)["packet"], "bcc-2026-10-27-regular-agenda-packet")

    def test_new_never_replaces_an_archived_index(self):
        tid = "bcc-2026-10-27-regular-agenda-packet-text"
        lid, _ = pk.write_new(tid, "ericdf", self.root, "2026-10-28")
        path = os.path.join(self.root, "manifest", lid + ".json")
        before = read_json(path)
        pk.write_new(tid, "someone-else", self.root, "2026-11-30")      # identical rerun: no change
        self.assertEqual(read_json(path), before)
        changed = dict(before, sha256="0" * 64)
        write(path, json.dumps(changed))
        with self.assertRaisesRegex(SystemExit, "not replaced"):
            pk.write_new(tid, "ericdf", self.root, "2026-11-30")
        self.assertEqual(read_json(path), changed)


class ArchivedIndexesReproduce(unittest.TestCase):
    def test_every_archived_index_reproduces(self):
        for path in glob.glob(os.path.join(REPO, "tools", "l2", "recipes", "*-agenda-packet-items.json")):
            recipe = read_json(path)
            data, _ = pk.build(recipe, REPO)
            with open(os.path.join(REPO, recipe["file"]), "rb") as f:
                self.assertEqual(f.read(), data, recipe["id"])


if __name__ == "__main__":
    unittest.main()
