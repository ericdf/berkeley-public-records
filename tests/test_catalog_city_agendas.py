"""tools/catalog_city_agendas.py and tools/meeting_map.py: the City's meeting listing, and its join
to the manifest and the Granicus catalog."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import catalog_city_agendas as cca  # noqa: E402
import meeting_map as mm  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "city_council_agendas.html"
FILES = "https://berkeleyca.gov/sites/default/files/city-council-meetings/"


class ParseListing(unittest.TestCase):
    def setUp(self):
        self.rows = {r["meeting"]: r for r in cca.parse(FIXTURE.read_text())}

    def test_regular_meeting_before_it_happens(self):
        r = self.rows["city-council-2026-10-27"]
        self.assertEqual((r["date"], r["label"], r["start_utc"]), ("2026-10-27", "regular", "2026-10-28T01:00:00Z"))
        self.assertEqual(r["city"]["agenda_packet"], FILES + "2026-10-27%20Agenda%20Packet%20-%20Council%20-%20WEB.pdf")
        self.assertIsNone(r["city"]["annotated_agenda"])
        self.assertIsNone(r["city"]["mp3"])

    def test_two_meetings_on_one_date(self):
        reg, spec = self.rows["city-council-2026-09-29"], self.rows["city-council-2026-09-29-0"]
        self.assertEqual((reg["label"], spec["label"]), ("regular", "special"))
        self.assertEqual(spec["city"]["agenda_packet"], FILES + "2026-09-29%20Special%20Agenda%20-%20Council.pdf")
        self.assertTrue(spec["city"]["annotated_agenda"].endswith("Special%20Annotated%20Agenda%20-%20Council.pdf"))
        self.assertTrue(reg["city"]["mp3"].endswith(".mp3") and reg["city"]["mp4"].endswith(".mp4"))

    def test_canceled_meeting(self):
        self.assertEqual(self.rows["city-council-2024-11-19-1"]["label"], "canceled")

    def test_header_row_is_skipped(self):
        self.assertEqual(len(self.rows), 4)


class Merge(unittest.TestCase):
    def test_vanished_meeting_is_kept_and_marked(self):
        a = {"meeting": "m1", "date": "2026-01-01"}
        b = {"meeting": "m2", "date": "2026-01-02"}
        first = cca.merge([], [a, b], "2026-10-01", {})
        second = cca.merge(first["entries"], [b], "2026-10-08", {})
        by = {e["meeting"]: e for e in second["entries"]}
        self.assertEqual((by["m1"]["first_listed"], by["m1"]["last_listed"]), ("2026-10-01", "2026-10-01"))
        self.assertEqual((by["m2"]["first_listed"], by["m2"]["last_listed"]), ("2026-10-01", "2026-10-08"))


class MeetingMap(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        os.makedirs(os.path.join(self.root, "manifest"))
        out = os.path.join(self.root, "catalog", "city-council-agendas", "meetings.json")
        cca.run(html_file=str(FIXTURE), today="2026-10-28", out=out)
        os.makedirs(os.path.join(self.root, "catalog", "granicus"))
        reg = {r["meeting"]: r for r in cca.parse(FIXTURE.read_text())}["city-council-2026-09-29"]
        with open(os.path.join(self.root, "catalog", "granicus", "city-council.json"), "w") as f:
            json.dump({"entries": [{"clip_id": "5719", "mp3_url": reg["city"]["mp3"], "mp4_url": None}]}, f)

    def put(self, mid, **fields):
        e = {"id": mid, "layer": "L0", "body": "Berkeley City Council", **fields}
        with open(os.path.join(self.root, "manifest", mid + ".json"), "w") as f:
            json.dump(e, f)

    def meeting(self, data, key):
        return next(m for m in data["meetings"] if m["meeting"] == key)

    def test_roles_join_by_url_label_and_recording(self):
        self.put("bcc-2026-09-29-special-and-regular-captions", doc_type="council_captions",
                 meeting_date="2026-09-29", meeting_label="Special and Regular")
        self.put("bcc-2026-09-29-regular-annotated-agenda", doc_type="annotated_agenda",
                 meeting_date="2026-09-29", meeting_label="regular")
        self.put("bcc-2026-10-27-regular-agenda-packet", doc_type="agenda_packet", meeting_date="2026-10-27",
                 meeting_label="regular", source_url=FILES + "2026-10-27%20Agenda%20Packet%20-%20Council%20-%20WEB.pdf")
        self.put("bcc-2026-10-27-item-21-staff-report", doc_type="staff_report", collection="staff_reports",
                 meeting_date="2026-10-27")
        self.put("bcc-2019-10-15-item-37-staff-report", doc_type="staff_report", collection="staff_reports",
                 meeting_date="2019-10-15", meeting_label="regular")
        data = mm.build(self.root)
        reg, spec = self.meeting(data, "city-council-2026-09-29"), self.meeting(data, "city-council-2026-09-29-0")
        both = ["bcc-2026-09-29-special-and-regular-captions"]
        self.assertEqual((reg["archived"]["captions"], spec["archived"]["captions"]), (both, both))
        self.assertEqual(reg["archived"]["annotated_agenda"], ["bcc-2026-09-29-regular-annotated-agenda"])
        self.assertEqual(spec["archived"]["annotated_agenda"], [])
        self.assertEqual(reg["recording_clips"], ["5719"])
        oct27 = self.meeting(data, "city-council-2026-10-27")
        self.assertEqual(oct27["archived"]["agenda_packet"], ["bcc-2026-10-27-regular-agenda-packet"])
        self.assertEqual(oct27["archived"]["items"], ["bcc-2026-10-27-item-21-staff-report"])  # sole meeting that day
        self.assertEqual(data["unmatched"], ["bcc-2019-10-15-item-37-staff-report"])

    def test_gaps(self):
        self.put("bcc-2026-09-29-regular-captions", doc_type="council_captions",
                 meeting_date="2026-09-29", meeting_label="Regular")
        since, lines = mm.gaps(mm.build(self.root))
        self.assertEqual(since, "2026-09-29")
        text = "\n".join(lines)
        self.assertIn("city-council-2026-09-29       annotated agenda; agenda packet", text)
        self.assertIn("special agenda (no annotated agenda archived); caption transcript", text)
        self.assertIn("city-council-2026-10-27       agenda packet", text)
        self.assertNotIn("2024-11-19", text)  # canceled, and before the default start


if __name__ == "__main__":
    unittest.main()
