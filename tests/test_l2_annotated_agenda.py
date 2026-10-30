"""tools/l2/annotated_agenda.py: the structured record of an annotated agenda."""
import glob, hashlib, json, os, shutil, sys, tempfile, unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools", "l2"))
import annotated_agenda as aa  # noqa: E402

TEXT = """                     BERKELEY CITY COUNCIL MEETING
                            ANNOTATED AGENDA
Preliminary Matters
   Roll Call: 6:04 p.m.
   Present:     Kesarwani, Taplin, Tregub, O’Keefe, Blackaby, Lunaparra, Humbert, Ishii
   Absent:      Bartlett
   Councilmember Bartlett present at 6:20 p.m.
Consent Calendar
 1.    Contract: Street Paving
       From: City Manager
       Recommendation: Adopt a Resolution authorizing a contract.
       1. Authorize the City Manager to execute the contract.
       Financial Implications: $1,200,000
       Contact: Public Works, (510) 981-6300
       Action: Adopted Resolution No. 1–N.S.
 2.    Ordinance: Second Reading
       From: City Manager
       Recommendation: Adopt second reading of Ordinance No. 8,000–N.S.
       First Reading Vote: All Ayes.
       Contact: City Clerk
       Action: Adopted second reading of Ordinance No. 8,000–N.S.
Council Consent Items
 3.    Referral: Bike Lanes
       From: Councilmember Tregub (Author), Councilmember Taplin (Co-Author) and
       Mayor Ishii (Co-Sponsor)
       Recommendation: Refer to the City Manager.
       Contact: Igor Tregub, Councilmember, District 4, (510) 981-7140
       Action: Councilmembers Bartlett and Humbert added as co-
       sponsors. Approved recommendation.
   Tuesday, May 5, 2026          ANNOTATED AGENDA                     Page 2
Action Calendar – New Business
  Urgency Item: Resolution on Something Urgent
  From: Mayor Adena Ishii (Author)
  Recommendation: Adopt a resolution.
  Action: 2 speakers. M/S/C (Ishii/Blackaby) to adopt Resolution No. 2–N.S.
  Vote: All Ayes.
 4.    Surveillance Cameras
       From: City Manager
       Recommendation: Approve the locations.
       Action: 30 speakers. M/S/Failed (Lunaparra/Tregub) to refer the item back.
       Vote: Ayes – Lunaparra, Tregub; Noes – Kesarwani, Taplin, O’Keefe, Blackaby,
       Humbert, Ishii; Abstain – Bartlett; Absent – None.
       M/S/Carried (Humbert/Blackaby) to approve the locations.
       Vote: Ayes – Kesarwani, Taplin, O’Keefe, Blackaby, Humbert, Ishii, Bartlett,
       Tregub; Noes – Lunaparra
Old Business
 A.    Accessory Dwelling Units
       From: City Manager
       Action: M/S/C (Taplin/Ishii) to adopt the ordinance.
       Vote: Ayes – Taplin, O’Keefe, Blackaby, Humbert, Ishii, Bartlett, Lunaparra; Noes – None;
       Abstain – None; Absent – Kesarwani (recused for conflict of interest), Tregub (recused)
Public Comment – Items Not Listed on the Agenda
   M/S/C (Ishii/Taplin) to adjourn.
   Vote: All Ayes.
Communications
 Bike Lanes
   1. Jane Doe
   2. John Roe (3)
   3. 42 similarly worded form letters
 Item #4: Surveillance Cameras
   4. Pat Example, on behalf of a long organization name that wraps
      onto a second line
Supplemental Communications and Reports 1
 Item #4: Surveillance Cameras
   5. Submitted by Mayor Ishii
"""


class Parse(unittest.TestCase):
    def setUp(self):
        self.d = aa.parse(TEXT, REPO)
        self.by = {i["item"]: i for i in self.d["items"]}

    def test_items(self):
        self.assertEqual(list(self.by), ["1", "2", "3", "urgent-1", "4", "A"])   # "1. Authorize" is not an item
        self.assertEqual(self.by["3"]["section"], "Council Consent Items")
        self.assertEqual(self.by["A"]["section"], "Old Business")
        self.assertEqual(self.by["1"]["financial_implications"], "$1,200,000")
        self.assertEqual(self.by["urgent-1"]["heading"], "Resolution on Something Urgent")
        first, last = self.by["4"]["lines"]
        self.assertTrue(TEXT.split("\n")[first - 1].strip().startswith("4."))
        self.assertIn("Noes – Lunaparra", TEXT.split("\n")[last - 1])

    def test_attendance(self):
        a = self.d["attendance"]
        self.assertEqual(a["roll_call_time"], "6:04 p.m.")
        self.assertEqual(len(a["present"]), 8)
        self.assertEqual([p["member"] for p in a["absent"]], ["off-bartlett"])
        self.assertEqual(a["arrived_late"], [{"member": "off-bartlett", "as_printed": "Bartlett", "time": "6:20 p.m."}])

    def test_attribution_and_contact(self):
        it = self.by["3"]
        self.assertEqual([(x["member"], x["role"], x["order"]) for x in it["attribution"]],
                         [("off-tregub", "author", 1), ("off-taplin", "co-author", 2), ("off-ishii", "co-sponsor", 3)])
        self.assertEqual(it["contact"], "Igor Tregub, Councilmember, District 4, (510) 981-7140")
        self.assertEqual([x["member"] for x in it["added_cosponsors"]], ["off-bartlett", "off-humbert"])
        self.assertEqual(self.by["1"]["attribution"], [])          # staff items name no member

    def test_competing_and_final_motion(self):
        m = self.by["4"]["motions"]
        self.assertEqual([(x["kind"], x["disposition"], x["final"]) for x in m],
                         [("competing", "failed", False), ("main", "carried", True)])
        self.assertEqual([p["member"] for p in m[0]["movers"]], ["off-lunaparra", "off-tregub"])
        self.assertEqual([len(m[0]["vote"][k]) for k in ("ayes", "noes", "abstain", "absent")], [2, 6, 1, 0])
        self.assertEqual([p["member"] for p in m[1]["vote"]["noes"]], ["off-lunaparra"])
        self.assertEqual(self.by["4"]["speakers"], 30)

    def test_vote_stops_at_section_heading_and_adjournment_is_not_the_item_vote(self):
        v = self.by["4"]["motions"][-1]["vote"]
        self.assertEqual(len(v["ayes"]), 8)
        self.assertNotIn("Old Business", v["as_printed"])
        self.assertEqual(len(self.by["A"]["motions"]), 1)   # ends at non-agenda public comment

    def test_recused_printed_under_absent(self):
        v = self.by["A"]["motions"][0]["vote"]
        self.assertEqual([p["member"] for p in v["recused"]], ["off-kesarwani", "off-tregub"])
        self.assertEqual(v["absent"], [])
        self.assertIn("printed under Absent", v["normalization_notes"])

    def test_prior_first_reading_vote_is_not_this_meetings_vote(self):
        it = self.by["2"]
        self.assertEqual(it["motions"], [])
        self.assertTrue(it["prior_first_reading_vote"]["all_ayes"])

    def test_all_ayes(self):
        v = self.by["urgent-1"]["motions"][0]["vote"]
        self.assertTrue(v["all_ayes"])
        self.assertEqual(v["ayes"], [])

    def test_communications(self):
        g = self.d["communications"]
        self.assertEqual([(x["section"], x["heading"], x["items"]) for x in g],
                         [("Communications", "Bike Lanes", []), ("Communications", "Item #4: Surveillance Cameras", ["4"]),
                          ("Supplemental Communications and Reports 1", "Item #4: Surveillance Cameras", ["4"])])
        self.assertEqual([(e["count"], e["count_basis"]) for e in g[0]["entries"]],
                         [(1, "one entry"), (3, "parenthesized number"), (42, "similarly worded or form letters")])
        self.assertTrue(g[1]["entries"][0]["as_printed"].endswith("onto a second line"))

    def test_unknown_name_kept_as_printed(self):
        p = aa.Parser(REPO).person("Arreguin")
        self.assertEqual(p, {"member": None, "as_printed": "Arreguin"})

    def test_refusals(self):
        with self.assertRaisesRegex(SystemExit, "begins twice"):
            aa.parse(TEXT.replace(" 4.    Surveillance", " 1.    Surveillance"), REPO)
        with self.assertRaisesRegex(SystemExit, "no agenda items and no roll call"):
            aa.parse("nothing here\n", REPO)


def read_json(path):
    with open(path) as f:
        return json.load(f)


def archived(name):
    path = os.path.join(REPO, "L1", "annotated-agendas", name)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return f.read()


class RealAgendas(unittest.TestCase):
    """Cases the Council's own records settle, kept as regression tests."""

    def item(self, name, number):
        t = archived(name)
        if t is None:
            self.skipTest(f"{name} is not in this checkout")
        return next(i for i in aa.parse(t, REPO)["items"] if i["item"] == number)

    def test_flock_2025_03_18_item_11(self):
        m = self.item("bcc-2025-03-18-regular-annotated-agenda-text.txt", "11")["motions"]
        self.assertEqual([(x["kind"], x["final"]) for x in m], [("competing", False), ("main", True)])
        self.assertEqual([len(m[0]["vote"][k]) for k in ("ayes", "noes", "abstain")], [2, 6, 1])
        self.assertEqual([len(m[1]["vote"][k]) for k in ("ayes", "noes")], [8, 1])

    def test_recusals_2026_01_20_item_21(self):
        v = next(x for x in self.item("bcc-2026-01-20-regular-annotated-agenda-text.txt", "21")["motions"] if x["final"])["vote"]
        self.assertEqual(sorted(p["member"] for p in v["recused"]), ["off-kesarwani", "off-tregub"])
        self.assertEqual(v["absent"], [])

    def test_urgency_item_2026_03_24_is_its_own_item(self):
        self.assertEqual(self.item("bcc-2026-03-24-regular-annotated-agenda-text.txt", "24")["motions"], [])
        u = self.item("bcc-2026-03-24-regular-annotated-agenda-text.txt", "urgent-1")
        self.assertEqual([x["member"] for x in u["added_cosponsors"]], ["off-taplin", "off-bartlett", "off-blackaby"])


class NewAndCheck(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        for d in ("manifest", "registry", "L1/annotated-agendas"):
            os.makedirs(os.path.join(self.root, d))
        shutil.copy(os.path.join(REPO, "registry", "speakers.json"), os.path.join(self.root, "registry"))
        raw = TEXT.encode()
        rel = "L1/annotated-agendas/bcc-2026-05-05-regular-annotated-agenda-text.txt"
        with open(os.path.join(self.root, rel), "wb") as f:
            f.write(raw)
        for e in ({"id": "bcc-2026-05-05-regular-annotated-agenda", "layer": "L0", "doc_type": "annotated_agenda",
                   "body": "Berkeley City Council", "meeting_date": "2026-05-05", "meeting_label": "regular",
                   "title": "Annotated Agenda", "file": "x.pdf", "sha256": "a" * 64},
                  {"id": "bcc-2026-05-05-regular-annotated-agenda-text", "layer": "L1", "file": rel,
                   "sha256": hashlib.sha256(raw).hexdigest(),
                   "derived_from": {"id": "bcc-2026-05-05-regular-annotated-agenda", "sha256": "a" * 64}}):
            with open(os.path.join(self.root, "manifest", e["id"] + ".json"), "w") as f:
                json.dump(e, f)

    def test_new_writes_reproducible_record_and_never_replaces(self):
        tid = "bcc-2026-05-05-regular-annotated-agenda-text"
        lid, n = aa.write_new(tid, "ericdf", self.root, "2026-10-30")
        self.assertEqual((lid, n), ("bcc-2026-05-05-regular-annotated-agenda-record", 6))
        path = os.path.join(self.root, "manifest", lid + ".json")
        e = read_json(path)
        self.assertEqual((e["layer"], e["doc_type"]), ("L2", "annotated_agenda_record"))
        data, _ = aa.build(read_json(os.path.join(self.root, e["extraction"]["recipe"])), self.root)
        self.assertEqual(hashlib.sha256(data).hexdigest(), e["sha256"])
        aa.write_new(tid, "ericdf", self.root, "2026-11-30")              # identical rerun: no change
        self.assertEqual(read_json(path), e)
        with open(path, "w") as f:
            json.dump(dict(e, sha256="0" * 64), f)
        with self.assertRaisesRegex(SystemExit, "not replaced"):
            aa.write_new(tid, "ericdf", self.root, "2026-11-30")


class ArchivedRecordsReproduce(unittest.TestCase):
    def test_every_archived_record_reproduces(self):
        for path in glob.glob(os.path.join(REPO, "tools", "l2", "recipes", "*-annotated-agenda*-record.json")):
            recipe = read_json(path)
            data, _ = aa.build(recipe, REPO)
            with open(os.path.join(REPO, recipe["file"]), "rb") as f:
                self.assertEqual(f.read(), data, recipe["id"])


if __name__ == "__main__":
    unittest.main()
