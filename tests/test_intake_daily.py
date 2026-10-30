"""Tests for tools/intake_daily.py: the check schedule, what counts as new or revised, and the
metadata for Clerk transcripts. Standard library only, no network."""
import datetime as dt, json, os, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tools"))
import intake_daily as idy  # noqa: E402

D = dt.date.fromisoformat
URL = "https://berkeleyca.gov/sites/default/files/city-council-meetings/2026-10-27%20Annotated%20Agenda%20-%20Council.pdf"


def meeting(date="2026-10-27", label="regular", url=URL):
    return {"date": date, "label": label, "city": {"annotated_agenda": url}}


def held(date="2026-10-27", url=URL, retrieved="2026-10-29", id_="bcc-2026-10-27-regular-annotated-agenda"):
    return {id_: {"id": id_, "doc_type": "annotated_agenda", "meeting_date": date, "meeting_label": "regular",
                  "source_url": url, "retrieved": retrieved, "archived": retrieved, "sha256": "x"}}


class Schedule(unittest.TestCase):
    def test_tiers(self):
        m = D("2026-10-27")
        self.assertFalse(idy.due(m, D("2026-10-26")))
        self.assertTrue(idy.due(m, D("2026-11-17")))          # day 21
        self.assertFalse(idy.due(m, D("2026-11-18")))         # day 22, a Wednesday
        self.assertTrue(idy.due(m, D("2026-11-23")))          # day 27, a Monday
        self.assertFalse(idy.due(m, D("2027-01-04")))         # day 69, Monday, past 60
        self.assertTrue(idy.due(m, D("2027-01-04"), sweep=True))
        self.assertTrue(idy.quarterly(D("2027-01-01")))
        self.assertFalse(idy.quarterly(D("2027-02-01")))


class Annotated(unittest.TestCase):
    T = D("2026-10-30")

    def test_first_posting(self):
        p = idy.plan_annotated([meeting()], {}, self.T, False, lambda u: None)
        self.assertEqual(p[0][2], "first posting")

    def test_unchanged_is_left_alone(self):
        lm = dt.datetime(2026, 10, 29, 18, 3, tzinfo=dt.timezone.utc)
        self.assertEqual(idy.plan_annotated([meeting()], held(), self.T, False, lambda u: lm), [])

    def test_modified_after_retrieval(self):
        lm = dt.datetime(2026, 10, 30, 9, 0, tzinfo=dt.timezone.utc)
        p = idy.plan_annotated([meeting()], held(), self.T, False, lambda u: lm)
        self.assertEqual(p[0][3], "bcc-2026-10-27-regular-annotated-agenda")

    def test_relinked_file(self):
        p = idy.plan_annotated([meeting(url=URL.replace(".pdf", "_0.pdf"))], held(), self.T, False, lambda u: None)
        self.assertIn("different file", p[0][2])

    def test_not_due_is_not_checked(self):
        calls = []
        idy.plan_annotated([meeting(date="2026-08-01")], held(date="2026-08-01"), self.T, False,
                           lambda u: calls.append(u))
        self.assertEqual(calls, [])

    def test_next_id(self):
        m = held()
        self.assertEqual(idy.next_id("bcc-2026-10-27-regular-annotated-agenda", m), "bcc-2026-10-27-regular-annotated-agenda-v2")


class Captions(unittest.TestCase):
    SIDE = {"from": "City Clerk <clerk@berkeleyca.gov>", "subject": "RE: Transcript", "received": "2026-11-18"}

    def test_council(self):
        (stem, ext, meta), why = idy.caption_meta("BCC 2026-11-17 Special and Regular Captioning.pdf", self.SIDE)
        self.assertEqual(stem, "bcc-2026-11-17-special-and-regular-captions")
        self.assertEqual(meta["meeting_label"], "Special and Regular")
        self.assertEqual(meta["approved_by"], "ericdf")
        self.assertIn("received 2026-11-18", meta["source_note"])
        self.assertTrue(meta["notices"])

    def test_committee_vtt(self):
        (stem, ext, meta), why = idy.caption_meta("2026-10-22 Budget Committee Captioning Text- Policy Committee.cc.vtt",
                                                  dict(self.SIDE, **{"from": "policycommittee@berkeleyca.gov"}))
        self.assertEqual(stem, "bcc-budget-finance-committee-2026-10-22-regular-captions")
        self.assertEqual(ext, "vtt")
        self.assertNotIn("notices", meta)

    def test_no_sidecar_is_skipped(self):
        got, why = idy.caption_meta("BCC 2026-11-17 Regular Captioning.pdf", None)
        self.assertIsNone(got); self.assertIn("date received", why)

    def test_outside_sender_is_skipped(self):
        got, why = idy.caption_meta("BCC 2026-11-17 Regular Captioning.pdf", dict(self.SIDE, **{"from": "someone@example.com"}))
        self.assertIsNone(got)

    def test_plan_captions_held_and_new(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = os.path.join(d, "BCC 2026-11-17 Regular Captioning.pdf"), os.path.join(d, "BCC 2026-11-18 Special Captioning.pdf")
            open(a, "wb").write(b"old"); open(b, "wb").write(b"new")
            json.dump(self.SIDE, open(b + ".json", "w"))
            stage, skip, keep = idy.plan_captions(d, {"x": {"id": "x", "sha256": idy.sha256(a)}})
            self.assertEqual([os.path.basename(p) for p in keep], [os.path.basename(a)])
            self.assertEqual(stage[0][1], "bcc-2026-11-18-special-captions")


if __name__ == "__main__":
    unittest.main()
