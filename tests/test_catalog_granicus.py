"""tools/catalog_granicus.py: parsing the City's Granicus listing and merging catalog runs."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import catalog_granicus as cg  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "granicus_listing.html"


class ParseListing(unittest.TestCase):
    def setUp(self):
        self.rows = {r["clip_id"]: r for r in cg.parse(FIXTURE.read_text())}

    def test_current_meeting_row(self):
        r = self.rows["5719"]
        self.assertEqual(r["name"], "Regular City Council Meeting - 6:00 p.m.")
        self.assertEqual((r["date"], r["listed_date"], r["duration"]),
                         ("2026-09-29", "Sep 29, 2026 - 06:04 PM", "05h 11m"))
        self.assertEqual(r["player_url"], "https://berkeley.granicus.com/MediaPlayer.php?view_id=5&clip_id=5719")
        self.assertTrue(r["mp3_url"].endswith(".mp3") and r["mp4_url"].endswith(".mp4"))

    def test_old_row_without_agenda_link(self):
        r = self.rows["2118"]
        self.assertEqual(r["date"], "2012-02-14")
        self.assertIsNone(r["agenda_url"])


class Merge(unittest.TestCase):
    def test_vanished_clip_is_kept_and_marked(self):
        first = cg.merge([], [{"clip_id": "1", "date": "2026-01-01"}, {"clip_id": "2", "date": "2026-01-02"}],
                         "2026-09-30", {})
        second = cg.merge(first["entries"], [{"clip_id": "2", "date": "2026-01-02"}], "2026-10-07", {})
        by = {e["clip_id"]: e for e in second["entries"]}
        self.assertEqual(by["1"]["last_listed"], "2026-09-30")
        self.assertEqual(by["1"]["first_listed"], "2026-09-30")
        self.assertEqual((by["2"]["first_listed"], by["2"]["last_listed"]), ("2026-09-30", "2026-10-07"))


if __name__ == "__main__":
    unittest.main()
