"""Tests for L3 transcript structure files (tools/l3_structure.py). Standard library only.

  python3 -m unittest discover -s tests -v
"""
import copy, os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import l3_structure as l3  # noqa: E402

VTT = """This information provided by Zoom. The City of Berkeley cannot certify the
following text since we did not create it.

CITY OF BERKELEY

WEBVTT

1
00:00:10.000 --> 00:00:14.000
Boardroom: I'm calling to order the Berkeley City Council meeting.

2
00:02:00.000 --> 00:02:05.000
Boardroom: Moving on to item number 12, the budget referral.

3
00:03:00.000 --> 00:03:09.000
Jane Doe: I live in District 4 and I support item 12.

4
00:04:00.000 --> 00:04:05.000
Boardroom: Is there a motion to adjourn?
"""
CART = """>> MAYOR ISHII: GOOD EVENING. WE ARE CALLING THE MEETING TO ORDER.
ROLL CALL, PLEASE.
>> MAYOR ISHII: WE WILL NOW MOVE TO ITEM 1, THE RESOLUTION.
>> SPEAKER: I OPPOSE THE
This information provided by a Certified Realtime Reporter. The City of Berkeley
cannot certify the following text since we did not create it.
RESOLUTION.
>> MAYOR ISHII: IS THERE A MOTION TO ADJOURN?
"""


def seg(**k):
    base = dict(meeting="2026-04-14 regular", kind="item", item=None, public_comment=None, level="inferred")
    base.update(k)
    return base


GOOD_VTT = dict(notice=l3.NOTICE, producer="tester", segments=[
    seg(start="00:00:10", end="00:02:00", kind="opening", evidence_start="I'm calling to order the Berkeley City Council"),
    seg(start="00:02:00", end="00:04:00", item="12", evidence_start="Moving on to item number 12",
        public_comment=[dict(start="00:03:00", end="00:03:09")]),
    seg(start="00:04:00", end="00:04:05", kind="closing", evidence_start="Boardroom: Is there a motion to adjourn?"),
])
GOOD_CART = dict(notice=l3.NOTICE, producer="tester", segments=[
    seg(start_line=1, end_line=2, kind="opening", evidence_start="GOOD EVENING. WE ARE CALLING"),
    seg(start_line=3, end_line=7, item="1", evidence_start=">> MAYOR ISHII: WE WILL NOW MOVE TO ITEM 1",
        public_comment=[dict(start_line=4, end_line=7)]),
    seg(start_line=8, end_line=8, kind="closing", evidence_start="IS THERE A MOTION TO ADJOURN"),
])


class TestStructure(unittest.TestCase):
    def test_good_files_pass(self):
        self.assertEqual(l3.check(GOOD_VTT, VTT), [])
        self.assertEqual(l3.check(GOOD_CART, CART), [])

    def test_vtt_after_cover_page(self):
        self.assertEqual(l3.parse_text(VTT)[0], "vtt")
        self.assertEqual(l3.parse_text(CART)[0], "cart")

    def test_quote_across_the_city_notice(self):
        d = copy.deepcopy(GOOD_CART)
        d["segments"][1]["evidence_start"] = "I OPPOSE THE RESOLUTION"
        self.assertEqual(l3.check(d, CART), [])

    def test_ellipsis_parts_in_order(self):
        self.assertTrue(l3.found("calling … City Council", l3.norm("i m calling to order the berkeley city council")))
        self.assertFalse(l3.found("City Council … calling", l3.norm("i m calling to order the berkeley city council")))

    def test_quote_must_be_at_its_segment(self):
        d = copy.deepcopy(GOOD_VTT)
        d["segments"][0]["evidence_start"] = "Is there a motion to adjourn?"
        self.assertTrue(any("evidence_start" in m for m in l3.check(d, VTT)))

    def test_notice_and_producer(self):
        d = copy.deepcopy(GOOD_VTT)
        d["notice"] = "x"; del d["producer"]
        msgs = l3.check(d, VTT)
        self.assertTrue(any("notice" in m for m in msgs) and any("producer" in m for m in msgs))

    def test_overlap_and_order(self):
        d = copy.deepcopy(GOOD_VTT)
        d["segments"][1]["start"] = "00:01:00"
        self.assertTrue(any("before the previous" in m for m in l3.check(d, VTT)))
        d = copy.deepcopy(GOOD_CART)
        d["segments"][2].update(start_line=9, end_line=99)
        self.assertTrue(any("past the end" in m for m in l3.check(d, CART)))

    def test_public_comment_inside_segment(self):
        d = copy.deepcopy(GOOD_VTT)
        d["segments"][1]["public_comment"] = [dict(start="00:03:00", end="00:04:30")]
        self.assertTrue(any("outside its segment" in m for m in l3.check(d, VTT)))

    def test_anchor_matches_format(self):
        d = copy.deepcopy(GOOD_CART)
        self.assertTrue(any("HH:MM:SS" in m for m in l3.check(d, VTT)))

    def test_vocabulary(self):
        d = copy.deepcopy(GOOD_VTT)
        d["segments"][1].update(kind="budget", meeting="2026-04-14 Regular", level="sure")
        msgs = l3.check(d, VTT)
        self.assertTrue(any("kind" in m for m in msgs) and any("meeting" in m for m in msgs)
                        and any("level" in m for m in msgs))

    def test_verified_needs_video(self):
        d = copy.deepcopy(GOOD_VTT)
        d["segments"][1]["level"] = "verified"
        self.assertEqual(sum("verified segment" in m for m in l3.check(d, VTT)), 3)


if __name__ == "__main__":
    unittest.main()
