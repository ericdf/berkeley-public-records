"""Tests for the contributor inbox, the 25 MB guard, and the large-file tool. Standard library
only, no network; the text-layer tests skip if pdftotext is not installed.

  python3 -m unittest discover -s tests -v
"""
import contextlib, io, json, os, shutil, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "tools"))

import process_inbox as pi  # noqa: E402
import check_large_files as clf  # noqa: E402
import ingest_large as il  # noqa: E402
import validate  # noqa: E402

HAVE_PDFTOTEXT = shutil.which("pdftotext") is not None


def drive_about(cmd, free=10 * 2**30, total=15 * 2**30):
    """A faked `rclone about --json` reply, or None for any other command."""
    if cmd[:2] == ["rclone", "about"]:
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"free": free, "total": total}))
    return None
BIG = pi.MAX_IN_GIT + 1


def make_pdf(lines):
    """A minimal one-page PDF; with lines, a real text layer pdftotext can read."""
    content = "BT /F1 11 Tf 72 720 Td 14 TL " + " ".join(
        "(" + l.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ") '" for l in lines) + " ET"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = "%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n"
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    return out.encode("latin-1")


TEXT_PDF = make_pdf([f"Line {i}: the City Council adopted the budget resolution on this date." for i in range(8)])
BLANK_PDF = make_pdf([])

L0_URL = {"layer": "L0", "collection": "sources/budgets", "title": "Mid-Year Budget Update FY2027",
          "document_date": "2027-02-10", "publisher": "City of Berkeley — Budget Office",
          "source_url": "https://berkeleyca.gov/sites/default/files/2027-02/mid-year.pdf",
          "retrieved": "2027-02-11"}
L0_CLERK = {"layer": "L0", "collection": "clerk", "title": "Certified copy of Resolution 71,000-N.S.",
            "document_date": "2026-09-01", "publisher": "City of Berkeley — City Clerk",
            "source_note": "Requested in person from the City Clerk; received 2026-09-12."}
T0 = {"layer": "T0", "collection": "press", "title": "Council adopts budget (Example News)",
      "document_date": "2026-06-30", "publisher": "Example News, a news organization",
      "relevance": "Reporting on the Council's June 30, 2026 budget adoption.",
      "source_url": "https://example.org/2026/06/30/budget"}


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        for d in ("manifest", "inbox", "registry"):
            os.makedirs(os.path.join(self.root, d))
        with open(os.path.join(self.root, "registry", "speakers.json"), "w") as f:
            json.dump({"officeholders": [], "staff_roles": []}, f)
        with open(os.path.join(self.root, "inbox", "README.md"), "w") as f:
            f.write("instructions\n")
        # One existing entry, for collision and duplicate tests.
        os.makedirs(os.path.join(self.root, "L0", "sources", "budgets"))
        existing = os.path.join(self.root, "L0", "sources", "budgets", "budgets-2026-old.pdf")
        with open(existing, "wb") as f:
            f.write(b"%PDF-1.4 existing document\n%%EOF\n")
        pi.write_json(os.path.join(self.root, "manifest", "budgets-2026-old.json"), {
            "id": "budgets-2026-old", "layer": "L0", "doc_type": "budget_document",
            "collection": "budgets", "title": "Old", "file": "L0/sources/budgets/budgets-2026-old.pdf",
            "sha256": pi.sha256_file(existing), "bytes": os.path.getsize(existing),
            "source_url": "https://berkeleyca.gov/old.pdf", "archived": "2026-09-30",
            "notices": [], "contributed_by": "ericdf"})

    def tearDown(self):
        shutil.rmtree(self.root)

    def drop(self, name, meta, data=TEXT_PDF, ext="pdf"):
        with open(os.path.join(self.root, "inbox", f"{name}.{ext}"), "wb") as f:
            f.write(data)
        with open(os.path.join(self.root, "inbox", f"{name}.json"), "w") as f:
            json.dump(meta, f)

    def run_process(self):
        return pi.process(self.root, "contributor1", today="2026-10-01", log=lambda *a: None)

    def entry(self, mid):
        with open(os.path.join(self.root, "manifest", mid + ".json")) as f:
            return json.load(f)

    def inbox_left(self):
        return sorted(n for n in os.listdir(os.path.join(self.root, "inbox")) if n != "README.md")

    def validate_errors(self):
        old_root, old_errors = validate.ROOT, list(validate.errors)
        validate.ROOT, validate.errors[:] = self.root, []
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                try:
                    validate.main()
                except SystemExit:
                    pass
            return list(validate.errors)
        finally:
            validate.ROOT = old_root
            validate.errors[:] = old_errors


class TestProcessInbox(Base):
    def test_l0_with_url(self):
        self.drop("a", L0_URL)
        r = self.run_process()
        mid = "budgets-2027-02-10-mid-year-budget-update-fy2027"
        self.assertEqual([a["id"] for a in r["added"]], [mid])
        e = self.entry(mid)
        self.assertEqual(e["file"], f"L0/sources/budgets/{mid}.pdf")
        self.assertEqual(e["doc_type"], "budget_document")      # from the existing collection
        self.assertEqual(e["collection"], "budgets")
        self.assertEqual(e["source_url"], L0_URL["source_url"])
        self.assertEqual(e["retrieved"], "2027-02-11")
        self.assertEqual(e["archived"], "2026-10-01")
        self.assertEqual(e["contributed_by"], "contributor1")
        self.assertEqual(e["sha256"], pi.sha256_file(os.path.join(self.root, e["file"])))
        self.assertEqual(self.inbox_left(), [])
        with open(os.path.join(self.root, "manifest", mid + ".json")) as f:
            raw = f.read()
        self.assertTrue(raw.endswith("}\n"))
        self.assertIn('  "id"', raw)
        self.assertEqual(self.validate_errors(), [])

    def test_l0_with_source_note(self):
        self.drop("clerk", L0_CLERK)
        r = self.run_process()
        mid = "clerk-2026-09-01-certified-copy-of-resolution-71-000-n-s"
        self.assertEqual(r["added"][0]["id"], mid)
        e = self.entry(mid)
        self.assertEqual(e["file"], f"L0/clerk/{mid}.pdf")
        self.assertIsNone(e["source_url"])
        self.assertEqual(e["source_note"], L0_CLERK["source_note"])
        self.assertEqual(self.validate_errors(), [])

    def test_needs_url_or_note(self):
        meta = dict(L0_CLERK)
        del meta["source_note"]
        self.drop("x", meta)
        with self.assertRaisesRegex(pi.Problem, "source_url.*source_note"):
            self.run_process()
        self.assertEqual(self.inbox_left(), ["x.json", "x.pdf"])   # nothing changed

    def test_t0_requires_publisher_and_relevance(self):
        meta = {k: v for k, v in T0.items() if k not in ("publisher", "relevance")}
        self.drop("t", meta)
        with self.assertRaises(pi.Problem) as cm:
            self.run_process()
        self.assertIn('"publisher"', str(cm.exception))
        self.assertIn('"relevance"', str(cm.exception))
        self.assertEqual(self.inbox_left(), ["t.json", "t.pdf"])

    def test_t0_filed_apart(self):
        self.drop("t", T0)
        r = self.run_process()
        e = self.entry(r["added"][0]["id"])
        self.assertTrue(e["file"].startswith("T0/press/press-2026-06-30-"))
        self.assertEqual(e["doc_type"], "third_party_publication")
        self.assertEqual(e["relevance"], T0["relevance"])
        if HAVE_PDFTOTEXT:
            t = self.entry(e["id"] + "-text")
            self.assertTrue(t["file"].startswith("L1/third-party/"))
            self.assertEqual(t["extraction"]["command"], "pdftotext -layout <T0> <L1>")
        self.assertEqual(self.validate_errors(), [])

    def test_undated_t0_with_wayback_url(self):
        wb = "https://web.archive.org/web/20181026150626/http://wellstoneclub.org/current-endorsements/"
        self.drop("u", dict(T0, document_date="undated", wayback_url=wb, collection="campaigns"))
        r = self.run_process()
        e = self.entry(r["added"][0]["id"])
        self.assertEqual(e["document_date"], "undated")
        self.assertEqual(e["wayback_url"], wb)
        self.assertTrue(e["id"].startswith("campaigns-council-adopts"))   # no date in the id
        self.assertEqual(self.validate_errors(), [])

    def test_undated_needs_t0_and_a_seen_date(self):
        self.drop("u", dict(T0, document_date="undated"))                # no wayback_url or retrieved
        with self.assertRaisesRegex(pi.Problem, "wayback_url"):
            self.run_process()
        self.drop("u", dict(L0_URL, document_date="undated"))
        with self.assertRaisesRegex(pi.Problem, "only for T0"):
            self.run_process()

    def test_bad_wayback_url_refused(self):
        self.drop("u", dict(T0, wayback_url="https://web.archive.org/web/2018/http://x"))
        with self.assertRaisesRegex(pi.Problem, "wayback_url"):
            self.run_process()

    def test_json_document_with_meta_json(self):
        meta = dict(L0_URL, title="Transaction query response", collection="sources/campaign_finance")
        with open(os.path.join(self.root, "inbox", "q.json"), "wb") as f:
            f.write(b'{"results": []}')
        with open(os.path.join(self.root, "inbox", "q.meta.json"), "w") as f:
            json.dump(meta, f)
        r = self.run_process()
        e = self.entry(r["added"][0]["id"])
        self.assertTrue(e["file"].endswith(".json"))
        self.assertEqual(self.validate_errors(), [])

    def test_duplicate_sha_refused(self):
        self.drop("dup", L0_URL, data=b"%PDF-1.4 existing document\n%%EOF\n")
        with self.assertRaisesRegex(pi.Problem, "already in the archive as budgets-2026-old"):
            self.run_process()

    def test_duplicate_within_inbox_refused(self):
        self.drop("one", L0_URL)
        self.drop("two", dict(L0_URL, title="Another title"))
        with self.assertRaisesRegex(pi.Problem, "same file as"):
            self.run_process()

    def test_id_collision_refused(self):
        self.drop("c", dict(L0_URL, id="budgets-2026-old"))
        with self.assertRaisesRegex(pi.Problem, "already used by budgets-2026-old"):
            self.run_process()

    def test_over_25mb_refused_and_left(self):
        path = os.path.join(self.root, "inbox", "big.pdf")
        with open(path, "wb") as f:
            f.truncate(BIG)                                   # sparse; nothing large on disk
        with open(os.path.join(self.root, "inbox", "big.json"), "w") as f:
            json.dump(L0_URL, f)
        with self.assertRaisesRegex(pi.Problem, "over 25 MB.*Large file"):
            self.run_process()
        self.assertEqual(self.inbox_left(), ["big.json", "big.pdf"])

    def test_missing_pair_and_bad_json(self):
        with open(os.path.join(self.root, "inbox", "lonely.pdf"), "wb") as f:
            f.write(TEXT_PDF)
        with open(os.path.join(self.root, "inbox", "broken.pdf"), "wb") as f:
            f.write(BLANK_PDF)
        with open(os.path.join(self.root, "inbox", "broken.json"), "w") as f:
            f.write('{"layer": "L0",, }')
        with self.assertRaises(pi.Problem) as cm:
            self.run_process()
        msg = str(cm.exception)
        self.assertIn("lonely.pdf has no metadata file", msg)
        self.assertIn("broken.json is not valid JSON", msg)

    def test_unknown_field_and_bad_date(self):
        self.drop("u", dict(L0_URL, sourceurl="x", document_date="Feb 10 2027"))
        with self.assertRaises(pi.Problem) as cm:
            self.run_process()
        self.assertIn("unknown field(s) sourceurl", str(cm.exception))
        self.assertIn("YYYY-MM-DD", str(cm.exception))

    def test_portal_releases_refused(self):
        self.drop("p", dict(L0_CLERK, collection="cpra"))
        with self.assertRaisesRegex(pi.Problem, "queue"):
            self.run_process()

    def test_captions_need_meeting_fields_and_notice(self):
        meta = {"layer": "L0", "collection": "council-captions", "title": "BCC Captioning",
                "document_date": "2026-10-07", "source_note": "Emailed by the Clerk; received 2026-10-08."}
        self.drop("cap", meta)
        with self.assertRaises(pi.Problem) as cm:
            self.run_process()
        for f in ("meeting_date", "meeting_label", "notices"):
            self.assertIn(f, str(cm.exception))
        self.drop("cap", dict(meta, meeting_date="2026-10-07", meeting_label="regular",
                              notices=["This information provided by a Certified Realtime Reporter."]))
        r = self.run_process()
        e = self.entry("bcc-2026-10-07-regular-captions")
        self.assertEqual(r["added"][0]["id"], e["id"])
        self.assertEqual(e["body"], "Berkeley City Council")
        self.assertNotIn("collection", e)

    def test_committee_captions_from_zoom_vtt(self):
        vtt = b"WEBVTT\r\n\r\n00:00:02.000 --> 00:00:15.000\r\nCall to order.\r\n"
        meta = {"layer": "L0", "collection": "council-captions", "title": "Budget Committee Captioning",
                "document_date": "2026-10-22", "meeting_date": "2026-10-22", "meeting_label": "regular",
                "body": "Planning Commission", "source_note": "Emailed by the Clerk; received 2026-10-29."}
        self.drop("cap", meta, data=vtt, ext="vtt")
        with self.assertRaisesRegex(pi.Problem, "the Council and its committees"):
            self.run_process()
        self.drop("cap", dict(meta, body="Berkeley City Council Budget & Finance Policy Committee"),
                  data=vtt, ext="vtt")
        r = self.run_process()
        e = self.entry("bcc-budget-finance-policy-committee-2026-10-22-regular-captions")
        self.assertEqual(r["added"][0]["id"], e["id"])
        self.assertEqual(e["body"], "Berkeley City Council Budget & Finance Policy Committee")
        self.assertEqual(e["doc_type"], "council_captions")
        self.assertEqual(e["file"], f"L0/council-captions/{e['id']}.vtt")
        self.assertEqual(e["notices"], [])
        t = self.entry(e["id"] + "-text")
        self.assertEqual(r["added"][0]["text"], t["id"])
        self.assertEqual(t["doc_type"], "council_captions_text")
        self.assertEqual(t["body"], e["body"])
        self.assertEqual((t["sha256"], t["extraction"]["command"]), (e["sha256"], "cp <L0> <L1>"))
        with open(os.path.join(self.root, t["file"]), "rb") as f:
            self.assertEqual(f.read(), vtt)
        self.assertEqual(self.validate_errors(), [])

    def test_captions_pdf_still_needs_notice_with_committee_body(self):
        self.drop("cap", {"layer": "L0", "collection": "council-captions", "title": "Captioning",
                          "document_date": "2026-10-22", "meeting_date": "2026-10-22",
                          "meeting_label": "regular", "source_note": "Emailed; received 2026-10-29.",
                          "body": "Berkeley City Council Budget & Finance Policy Committee"})
        with self.assertRaisesRegex(pi.Problem, "notices"):
            self.run_process()

    @unittest.skipUnless(HAVE_PDFTOTEXT, "pdftotext not installed")
    def test_text_layer_generated(self):
        self.drop("a", L0_URL)
        r = self.run_process()
        mid = r["added"][0]["id"]
        self.assertEqual(r["added"][0]["text"], mid + "-text")
        t = self.entry(mid + "-text")
        self.assertEqual(t["file"], f"L1/sources/budgets/{mid}-text.txt")
        self.assertEqual(t["doc_type"], "budget_document_text")
        self.assertEqual(t["derived_from"], {"id": mid, "sha256": self.entry(mid)["sha256"]})
        self.assertEqual(t["extraction"]["command"], "pdftotext -layout <L0> <L1>")
        self.assertTrue(t["extraction"]["tool"].startswith("pdftotext"))
        with open(os.path.join(self.root, t["file"])) as f:
            self.assertIn("adopted the budget resolution", f.read())
        self.assertEqual(self.validate_errors(), [])

    @unittest.skipUnless(HAVE_PDFTOTEXT, "pdftotext not installed")
    def test_text_layer_skipped_without_text(self):
        self.drop("s", L0_CLERK, data=BLANK_PDF)
        r = self.run_process()
        self.assertEqual(r["added"][0]["text"], "no text layer; needs OCR")
        self.assertFalse(os.path.exists(os.path.join(self.root, "manifest", r["added"][0]["id"] + "-text.json")))

    def test_validate_fails_on_unprocessed_inbox(self):
        self.drop("waiting", L0_URL)
        errs = self.validate_errors()
        self.assertTrue(any(e.startswith("inbox/waiting.pdf") for e in errs), errs)
        self.run_process()
        self.assertEqual(self.validate_errors(), [])



STAFF = {"layer": "L0", "collection": "staff-reports", "body": "Berkeley City Council",
         "title": "Contract: Example Services for Street Repair",
         "publisher": "City of Berkeley — Office of the City Manager; Public Works",
         "meeting_date": "2026-10-13", "meeting_label": "regular", "item": "Consent Calendar Item 7",
         "source_url": "https://berkeleyca.gov/sites/default/files/documents/2026-10-13%20Item%2007.pdf",
         "retrieved": "2026-10-01"}


class TestMeetingCollections(Base):
    def test_inbox_refuses_agenda_packets(self):
        self.drop("packet", {"layer": "L0", "collection": "agenda-packets", "title": "Agenda Packet",
                             "document_date": "2026-10-27", "meeting_date": "2026-10-27",
                             "meeting_label": "regular", "source_url": "https://berkeleyca.gov/p.pdf"})
        with self.assertRaisesRegex(pi.Problem, "Large file"):
            self.run_process()
        self.assertEqual(self.inbox_left(), ["packet.json", "packet.pdf"])

    def test_annotated_agenda_meeting_filled_from_the_city_catalog(self):
        url = "https://berkeleyca.gov/sites/default/files/city-council-meetings/2026-09-29%20Annotated%20Agenda.pdf"
        path = os.path.join(self.root, pi.CITY_MEETINGS)
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as f:
            json.dump({"entries": [{"meeting": "city-council-2026-09-29", "date": "2026-09-29", "label": "regular",
                                    "city": {"agenda_packet": None, "annotated_agenda": url}}]}, f)
        self.drop("annotated", {"layer": "L0", "collection": "annotated-agendas", "title": "Annotated Agenda",
                                "document_date": "2026-09-29", "source_url": url, "retrieved": "2026-10-28"})
        self.run_process()
        e = self.entry("bcc-2026-09-29-regular-annotated-agenda")
        self.assertEqual((e["meeting_date"], e["meeting_label"]), ("2026-09-29", "regular"))


class TestMeetingBatches(Base):
    """ingest_large.py --meeting: packets planned from the City catalog, revised packets with
    their originals, and one batch pull request."""
    FILES = "https://berkeleyca.gov/sites/default/files/city-council-meetings/"

    def setUp(self):
        super().setUp()
        self.served = {}
        path = os.path.join(self.root, pi.CITY_MEETINGS)
        os.makedirs(os.path.dirname(path))
        self.entries = []
        with open(path, "w") as f:
            json.dump({"entries": self.entries}, f)

    def listing(self, slug, date, label, packet):
        self.entries.append({"meeting": slug, "date": date, "label": label,
                             "city": {"agenda_packet": self.FILES + packet, "annotated_agenda": None}})
        with open(os.path.join(self.root, pi.CITY_MEETINGS), "w") as f:
            json.dump({"entries": self.entries}, f)

    def fetch(self, fields, dest):
        url = fields["source_url"]
        if url not in self.served:
            raise il.urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        path = os.path.join(dest, "download.pdf")
        with open(path, "wb") as f:
            f.write(self.served[url])
        return path

    def plans(self, slug):
        return il.plan_meeting(self.root, slug, tempfile.mkdtemp(), "ericdf", fetch=self.fetch,
                               today="2026-10-28", wayback=lambda u: None)

    def test_plain_packet(self):
        self.listing("city-council-2026-10-27", "2026-10-27", "regular",
                     "2026-10-27%20Agenda%20Packet%20-%20Council%20-%20WEB.pdf")
        self.served[self.FILES + "2026-10-27%20Agenda%20Packet%20-%20Council%20-%20WEB.pdf"] = b"%PDF-1.4 a\n"
        [p] = self.plans("city-council-2026-10-27")
        e = p["entry"]
        self.assertEqual(e["id"], "bcc-2026-10-27-regular-agenda-packet")
        self.assertEqual(e["title"], "Agenda Packet, Berkeley City Council Regular Meeting, October 27, 2026")
        self.assertEqual((e["publisher"], e["meeting_label"]), (il.PUBLISHER, "regular"))
        self.assertNotIn("version_label", e)

    def test_revised_packet_with_its_original(self):
        listed = "2026-06-16%20Revised%20Agenda%20Packet%20-%20Council%20-%20WEB.pdf"
        self.listing("city-council-2026-06-16", "2026-06-16", "regular", listed)
        self.served[self.FILES + listed] = b"%PDF-1.4 revised\n"
        self.served[self.FILES + "2026-06-16%20Agenda%20Packet%20-%20Council%20-%20WEB.pdf"] = b"%PDF-1.4 original\n"
        orig, rev = self.plans("city-council-2026-06-16")
        self.assertEqual(orig["entry"]["id"], "bcc-2026-06-16-regular-agenda-packet")
        self.assertEqual(orig["entry"]["revised_by"], ["bcc-2026-06-16-regular-agenda-packet-revised"])
        self.assertIn("No longer linked", orig["entry"]["source_note"])
        self.assertEqual(rev["entry"]["id"], "bcc-2026-06-16-regular-agenda-packet-revised")
        self.assertEqual((rev["entry"]["revision_of"], rev["entry"]["version_label"]),
                         ("bcc-2026-06-16-regular-agenda-packet", "Revised"))
        self.assertTrue(rev["entry"]["title"].startswith("Revised Agenda Packet"))
        calls = []

        def run(cmd, check=True, cwd=None, **kw):
            if drive_about(cmd):
                return drive_about(cmd)
            calls.append(cmd)
            if cmd[:2] == ["rclone", "copyto"] and cmd[2].startswith(il.REMOTE):
                src = orig if orig["entry"]["id"] in cmd[2] and "revised" not in cmd[2] else rev
                shutil.copy(src["staged"], cmd[3])
            return subprocess.CompletedProcess(cmd, 1 if cmd[:2] == ["git", "rev-parse"] or cmd[:2] == ["git", "ls-remote"] else 0)
        with contextlib.redirect_stdout(io.StringIO()):
            il.execute_batch(self.root, [orig, rev], run=run, open_pr=True, today="2026-10-28", branch="large-file/test")
        first_upload = next(k for k, c in enumerate(calls) if c[0] == "rclone")
        self.assertIn(["git", "switch", "-c", "large-file/test", "origin/main"], calls[:first_upload])
        pr = [c for c in calls if c[:3] == ["gh", "pr", "create"]][0]
        self.assertIn("--draft", pr)
        self.assertEqual([x for x in self.validate_errors() if "not verified" not in x], [])

    def test_revised_packet_whose_original_is_gone(self):
        listed = "2026-07-14%20Revised%20Agenda%20Packet%20-%20Council%20-%20WEB.pdf"
        self.listing("city-council-2026-07-14", "2026-07-14", "regular", listed)
        self.served[self.FILES + listed] = b"%PDF-1.4 revised only\n"
        [rev] = self.plans("city-council-2026-07-14")
        self.assertNotIn("revision_of", rev["entry"])
        self.assertIn("original was not located", rev["entry"]["version_note"])
        self.assertIn("404", rev["entry"]["version_note"])

    def test_meeting_already_archived_is_skipped(self):
        url = "2026-10-27%20Agenda%20Packet%20-%20Council%20-%20WEB.pdf"
        self.listing("city-council-2026-10-27", "2026-10-27", "regular", url)
        pi.write_json(os.path.join(self.root, "manifest", "x.json"), {"id": "x", "source_url": self.FILES + url})
        self.assertEqual(self.plans("city-council-2026-10-27"), [])


class TestVersions(Base):
    def test_inbox_revision_links_both_ways_and_validator_checks_it(self):
        self.drop("v1", {"layer": "L0", "collection": "annotated-agendas", "title": "Annotated Agenda",
                         "document_date": "2026-07-28", "meeting_date": "2026-07-28", "meeting_label": "regular",
                         "source_url": "https://berkeleyca.gov/a.pdf"})
        self.run_process()
        self.drop("v2", {"layer": "L0", "collection": "annotated-agendas", "title": "Annotated Agenda",
                         "document_date": "2026-07-28", "meeting_date": "2026-07-28", "meeting_label": "regular",
                         "source_url": "https://berkeleyca.gov/a_0.pdf", "id": "bcc-2026-07-28-regular-annotated-agenda-v2",
                         "revision_of": "bcc-2026-07-28-regular-annotated-agenda",
                         "version_note": "The City replaced the file without labeling it."},
                  data=make_pdf(["Line: a different version of the annotated agenda, with corrected text."] * 6))
        self.run_process()
        old = self.entry("bcc-2026-07-28-regular-annotated-agenda")
        self.assertEqual(old["revised_by"], ["bcc-2026-07-28-regular-annotated-agenda-v2"])
        self.assertEqual(self.validate_errors(), [])
        old["revised_by"] = []
        pi.write_json(os.path.join(self.root, "manifest", old["id"] + ".json"), old)
        errors = self.validate_errors()
        self.assertTrue(any("does not list" in x for x in errors), errors)

    def test_inbox_refuses_unknown_revision_of(self):
        self.drop("v2", {"layer": "L0", "collection": "annotated-agendas", "title": "Annotated Agenda",
                         "document_date": "2026-07-28", "meeting_date": "2026-07-28", "meeting_label": "regular",
                         "source_url": "https://berkeleyca.gov/a_0.pdf", "revision_of": "no-such-id"})
        with self.assertRaisesRegex(pi.Problem, "not an archive id"):
            self.run_process()


class TestStaffReports(Base):
    def test_council_staff_report(self):
        self.drop("s", STAFF)
        r = self.run_process()
        mid = "bcc-2026-10-13-item-07-staff-report"
        self.assertEqual([a["id"] for a in r["added"]], [mid])
        e = self.entry(mid)
        self.assertEqual(list(e), ["id", "layer", "doc_type", "collection", "body", "publisher",
                                   "title", "meeting_date", "meeting_label", "item", "file", "sha256",
                                   "bytes", "source_url", "retrieved", "archived", "wayback_url",
                                   "notices", "contributed_by"])
        self.assertEqual(e["file"], f"L0/staff-reports/{mid}.pdf")
        self.assertEqual((e["doc_type"], e["collection"], e["body"]),
                         ("staff_report", "staff_reports", "Berkeley City Council"))
        self.assertNotIn("document_date", e)
        if HAVE_PDFTOTEXT:
            t = self.entry(mid + "-text")
            self.assertEqual(t["file"], f"L1/staff-reports/{mid}-text.txt")
            self.assertEqual((t["doc_type"], t["body"], t["meeting_label"]),
                             ("staff_report_text", "Berkeley City Council", "regular"))
        self.assertEqual(self.validate_errors(), [])

    def test_special_meeting_and_part(self):
        self.drop("s", dict(STAFF, meeting_label="special", item="Special Meeting Item 1",
                            doc_type="presentation"))
        self.drop("t", dict(STAFF, item="Action Calendar Item 26, Supplemental #2",
                            doc_type="supplemental_agenda_material",
                            part="supplemental-agenda-material-supp-2"), data=make_pdf(["other"]))
        ids = sorted(a["id"] for a in self.run_process()["added"])
        self.assertEqual(ids, ["bcc-2026-10-13-item-26-supplemental-agenda-material-supp-2",
                               "bcc-2026-10-13-special-item-01-presentation"])

    def test_commission_item_letter_and_no_label(self):
        meta = {k: v for k, v in STAFF.items() if k != "meeting_label"}
        self.drop("s", dict(meta, body="Planning Commission", item="Item 10B",
                            doc_type="agenda_material", part="chairs-memo"))
        mid = self.run_process()["added"][0]["id"]
        self.assertEqual(mid, "planning-commission-2026-10-13-item-10b-chairs-memo")
        self.assertNotIn("meeting_label", self.entry(mid))

    def test_committee_prefix_follows_existing_entries(self):
        body = "Berkeley City Council Budget & Finance Policy Committee"
        pi.write_json(os.path.join(self.root, "manifest", "bcc-budget-finance-committee-2026-04-09-item-03-presentation.json"),
                      {"id": "bcc-budget-finance-committee-2026-04-09-item-03-presentation", "layer": "L0",
                       "doc_type": "presentation", "collection": "staff_reports", "body": body,
                       "meeting_date": "2026-04-09", "file": None, "sha256": "0" * 64})
        self.assertEqual(pi.body_prefix(body, pi.load_manifest(self.root)), "bcc-budget-finance-committee")
        self.assertEqual(pi.body_prefix(body, {}), "bcc-budget-finance-policy-committee")
        self.assertEqual(pi.body_prefix("Environment & Climate Commission", {}),
                         "environment-climate-commission")

    def test_staff_report_requirements(self):
        self.drop("s", {k: v for k, v in STAFF.items()
                        if k not in ("body", "meeting_label", "item", "meeting_date")})
        with self.assertRaises(pi.Problem) as cm:
            self.run_process()
        for f in ('"body"', '"meeting_date"', '"item"'):
            self.assertIn(f, str(cm.exception))
        self.assertEqual(self.inbox_left(), ["s.json", "s.pdf"])

    def test_council_needs_label_and_known_doc_type(self):
        meta = {k: v for k, v in STAFF.items() if k != "meeting_label"}
        self.drop("s", dict(meta, doc_type="memo"))
        with self.assertRaises(pi.Problem) as cm:
            self.run_process()
        self.assertIn('"meeting_label"', str(cm.exception))
        self.assertIn('"doc_type" is "memo"', str(cm.exception))

    def test_body_only_for_staff_reports(self):
        self.drop("a", dict(L0_URL, body="Berkeley City Council"))
        with self.assertRaisesRegex(pi.Problem, '"body" is only for the staff-reports'):
            self.run_process()

class TestLargeFileGuard(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp()
        subprocess.run(["git", "init", "-q", self.repo], check=True)
        for k, v in (("user.name", "t"), ("user.email", "t@example.org"), ("commit.gpgsign", "false")):
            subprocess.run(["git", "-C", self.repo, "config", k, v], check=True)

    def tearDown(self):
        shutil.rmtree(self.repo)

    def add(self, name, size):
        with open(os.path.join(self.repo, name), "wb") as f:
            f.truncate(size)
        subprocess.run(["git", "-C", self.repo, "add", name], check=True)

    def test_oversized(self):
        self.assertEqual(clf.oversized([("a", 10), ("b", BIG), ("c", pi.MAX_IN_GIT)]), [("b", BIG)])

    def test_staged_check_and_hook(self):
        self.add("small.pdf", 1000)
        self.assertEqual(clf.oversized(clf.staged(cwd=self.repo)), [])
        self.add("big.pdf", BIG)
        self.assertEqual(clf.oversized(clf.staged(cwd=self.repo)), [("big.pdf", BIG)])
        # The installed hook script itself refuses the commit.
        shutil.copytree(os.path.join(REPO, "tools"), os.path.join(self.repo, "tools"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        subprocess.run(["git", "-C", self.repo, "config", "core.hooksPath", "tools/hooks"], check=True)
        p = subprocess.run(["git", "-C", self.repo, "commit", "-q", "-m", "x"], capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("big.pdf", p.stderr)
        self.assertIn("over 25 MB", p.stderr)
        subprocess.run(["git", "-C", self.repo, "rm", "-q", "--cached", "big.pdf"], check=True)
        p = subprocess.run(["git", "-C", self.repo, "commit", "-q", "-m", "x"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_range_check_sees_deleted_files(self):
        self.add("a.txt", 10)
        subprocess.run(["git", "-C", self.repo, "commit", "-q", "-m", "base", "--no-verify"], check=True)
        base = subprocess.run(["git", "-C", self.repo, "rev-parse", "HEAD"], capture_output=True,
                              text=True).stdout.strip()
        self.add("big.pdf", BIG)
        subprocess.run(["git", "-C", self.repo, "commit", "-q", "-m", "big", "--no-verify"], check=True)
        subprocess.run(["git", "-C", self.repo, "rm", "-q", "big.pdf"], check=True)
        subprocess.run(["git", "-C", self.repo, "commit", "-q", "-m", "gone", "--no-verify"], check=True)
        self.assertEqual(clf.oversized(clf.in_range(f"{base}..HEAD", cwd=self.repo)), [("big.pdf", BIG)])


ISSUE_BODY = """### Document title

Presentation on Potential Ballot Measures

### Document date

2026-05-19

### Layer

L0 — made or published by a public body (the City, a commission, the County)

### Publisher

City of Berkeley — Office of the City Manager

### City web address

https://berkeleyca.gov/sites/default/files/2026-05/presentation.pdf

### Google Drive link

_No response_

### How you obtained it

_No response_

### Relevance (T0 only)

_No response_

### Collection, if you know it

sources/ballot_2026

### Size, if you know it

48 MB

### Anything else

_No response_
"""


class TestIngestLarge(Base):
    def issue(self, body=ISSUE_BODY):
        return {"number": 42, "title": "Large file: presentation", "body": body,
                "author": {"login": "contributor1"}}

    def test_parse_issue_body(self):
        f = il.parse_issue_body(ISSUE_BODY)
        self.assertEqual(f["layer"], "L0")
        self.assertEqual(f["collection"], "sources/ballot_2026")
        self.assertIsNone(f["drive_link"])
        self.assertIsNone(f["source_note"])
        self.assertEqual(f["document_date"], "2026-05-19")

    def test_drive_file_id(self):
        self.assertEqual(il.drive_file_id("https://drive.google.com/file/d/1AbC_dEf-GhIjK/view?usp=sharing"),
                         "1AbC_dEf-GhIjK")
        self.assertEqual(il.drive_file_id("https://drive.google.com/open?id=1AbC_dEf-GhIjK"), "1AbC_dEf-GhIjK")

    def fake_fetch(self, size, data=b"%PDF-1.4 large\n"):
        def fetch(fields, dest):
            path = os.path.join(dest, "presentation.pdf")
            with open(path, "wb") as f:
                f.write(data)
                f.truncate(size)
            return path
        return fetch

    def test_dry_run_plans_and_changes_nothing(self):
        staging = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, staging)
        before = sorted(os.listdir(os.path.join(self.root, "manifest")))
        p = il.plan(self.root, self.issue(), staging, fetch=self.fake_fetch(BIG), today="2026-10-01")
        e = p["entry"]
        self.assertEqual(e["id"], "ballot-2026-2026-05-19-presentation-on-potential-ballot-measures")
        self.assertIsNone(e["file"])
        self.assertEqual(e["bytes"], BIG)
        self.assertEqual(e["contributed_by"], "contributor1")
        self.assertEqual(e["retrieved"], "2026-10-01")
        self.assertEqual(p["ia_item"], il.ia_identifier(e["id"]))
        self.assertLessEqual(len(p["ia_item"]), 80)
        self.assertTrue(p["staged"].endswith(e["id"] + ".pdf"))
        self.assertIn("PUBLIC AND PERMANENT", il.describe(p))
        self.assertEqual(sorted(os.listdir(os.path.join(self.root, "manifest"))), before)

    def test_ia_identifier_fits_the_internet_archive_limit(self):
        short = "bcc-2025-03-25-item-08-staff-report"
        self.assertEqual(il.ia_identifier(short), "berkeley-public-records-" + short)
        long_id = "waterfront-2024-11-08-draft-waterfront-specific-plan-for-the-city-of-berkeley-public"
        item = il.ia_identifier(long_id)
        self.assertLessEqual(len(item), 80)
        self.assertTrue(item.startswith("berkeley-public-records-waterfront-2024-11-08-"))
        self.assertEqual(item, il.ia_identifier(long_id))
        self.assertNotEqual(item, il.ia_identifier(long_id + "-x"))
        self.assertRegex(item, r"^[a-z0-9-]+$")

    def test_refuses_small_files_and_duplicates(self):
        staging = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, staging)
        with self.assertRaisesRegex(il.Problem, "not over 25 MB"):
            il.plan(self.root, self.issue(), staging, fetch=self.fake_fetch(1000))
        dup = b"%PDF-1.4 existing document\n%%EOF\n"
        with self.assertRaisesRegex(il.Problem, "already in the archive as budgets-2026-old"):
            il.plan(self.root, self.issue(), staging, fetch=self.fake_fetch(len(dup), dup))

    def test_issue_can_supply_an_archive_id(self):
        body = ISSUE_BODY + "\n### Archive id, if needed\n\nballot-2026-2026-05-19-presentation-part-2\n"
        p = il.plan(self.root, self.issue(body), tempfile.mkdtemp(), fetch=self.fake_fetch(BIG))
        self.assertEqual(p["entry"]["id"], "ballot-2026-2026-05-19-presentation-part-2")

    def test_needs_a_source(self):
        body = ISSUE_BODY.replace("https://berkeleyca.gov/sites/default/files/2026-05/presentation.pdf",
                                  "_No response_")
        with self.assertRaisesRegex(il.Problem, "neither a City web address nor a Google Drive link"):
            il.plan(self.root, self.issue(body), tempfile.gettempdir(), fetch=self.fake_fetch(BIG))

    def test_upload_path_with_fake_commands(self):
        """execute() with every external command faked: no network, nothing uploaded."""
        staging = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, staging)
        p = il.plan(self.root, self.issue(), staging, fetch=self.fake_fetch(BIG), today="2026-10-01")
        calls = []

        def run(cmd, check=True, cwd=None, **kw):
            if drive_about(cmd):
                return drive_about(cmd)
            calls.append(cmd)
            if cmd[:2] == ["rclone", "copyto"] and cmd[2].startswith(il.REMOTE):
                shutil.copy(p["staged"], cmd[3])              # the verification download
        with contextlib.redirect_stdout(io.StringIO()):
            il.execute(self.root, p, run=run, today="2026-10-01")
        self.assertEqual([c[0] for c in calls], ["rclone", "rclone", "ia"])
        e = self.entry(p["entry"]["id"])
        ia, drive = e["blob"]["copies"]
        self.assertIsNone(ia["verified"])
        self.assertEqual(drive["verified"], "2026-10-01")
        self.assertEqual(self.validate_errors(), [])         # lands once the Drive copy is checked
        self.assertTrue(validate.pending_preservation(e))   # the IA copy is still to be checked
        self.assertEqual(il.pending_ia(self.root), [e["id"]])
        with open(p["staged"], "rb") as f:
            data = f.read()
        url = il.verify_ia(self.root, e["id"], stream=lambda u: iter([data]), today="2026-10-03")
        self.assertTrue(url.startswith("https://archive.org/download/" + p["ia_item"] + "/"))
        ia = self.entry(e["id"])["blob"]["copies"][0]
        self.assertEqual(ia["verified"], "2026-10-03")
        self.assertNotIn("verify_note", ia)
        self.assertEqual(self.validate_errors(), [])
        self.assertEqual(il.pending_ia(self.root), [])

    def test_refuses_upload_when_drive_lacks_room(self):
        staging = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, staging)
        p = il.plan(self.root, self.issue(), staging, fetch=self.fake_fetch(BIG), today="2026-10-01")
        calls = []

        def run(cmd, check=True, cwd=None, **kw):
            calls.append(cmd)
            return drive_about(cmd, free=BIG + il.DRIVE_MARGIN - 1)
        with self.assertRaisesRegex(il.Problem, "Nothing was uploaded"):
            il.execute(self.root, p, run=run, today="2026-10-01")
        self.assertEqual(calls, [["rclone", "about", il.REMOTE, "--json"]])
        self.assertFalse(os.path.exists(os.path.join(self.root, "manifest", p["entry"]["id"] + ".json")))

    def test_verify_ia_falls_back_to_the_storage_server(self):
        staging = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, staging)
        p = il.plan(self.root, self.issue(), staging, fetch=self.fake_fetch(BIG), today="2026-10-01")

        def run(cmd, check=True, cwd=None, **kw):
            if drive_about(cmd):
                return drive_about(cmd)
            if cmd[:2] == ["rclone", "copyto"] and cmd[2].startswith(il.REMOTE):
                shutil.copy(p["staged"], cmd[3])
        with contextlib.redirect_stdout(io.StringIO()):
            il.execute(self.root, p, run=run, today="2026-10-01")
        with open(p["staged"], "rb") as f:
            data = f.read()
        server = "https://ia600000.us.archive.org/2/items/x/" + p["entry"]["id"] + ".pdf"

        def stream(url):
            if url.startswith("https://archive.org/download/"):
                raise OSError("HTTP Error 404: Not Found")
            yield data
        old = il.ia_server_url
        il.ia_server_url = lambda item, path: server
        try:
            url = il.verify_ia(self.root, p["entry"]["id"], stream=stream, today="2026-10-02")
        finally:
            il.ia_server_url = old
        self.assertEqual(url, server)
        self.assertEqual(self.entry(p["entry"]["id"])["blob"]["copies"][0]["verified"], "2026-10-02")

    def test_verify_ia_refuses_a_mismatched_copy(self):
        staging = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, staging)
        p = il.plan(self.root, self.issue(), staging, fetch=self.fake_fetch(BIG), today="2026-10-01")

        def run(cmd, check=True, cwd=None, **kw):
            if drive_about(cmd):
                return drive_about(cmd)
            if cmd[:2] == ["rclone", "copyto"] and cmd[2].startswith(il.REMOTE):
                shutil.copy(p["staged"], cmd[3])
        with contextlib.redirect_stdout(io.StringIO()):
            il.execute(self.root, p, run=run, today="2026-10-01")
        before = self.entry(p["entry"]["id"])
        with self.assertRaisesRegex(il.Problem, "does not match"):
            il.verify_ia(self.root, p["entry"]["id"], stream=lambda u: iter([b"wrong bytes"]))
        self.assertEqual(self.entry(p["entry"]["id"]), before)

        def offline(url):
            raise OSError("404")
            yield b""
        old = il.ia_server_url
        il.ia_server_url = lambda item, path: None                 # no network in tests
        try:
            with self.assertRaisesRegex(il.Problem, "may not be public yet"):
                il.verify_ia(self.root, p["entry"]["id"], stream=offline)
        finally:
            il.ia_server_url = old

    def test_refuses_to_upload_when_the_branch_exists(self):
        staging = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, staging)
        p = il.plan(self.root, self.issue(), staging, fetch=self.fake_fetch(BIG), today="2026-10-01")
        calls = []

        def run(cmd, check=False, cwd=None, capture_output=False):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0 if cmd[:2] == ["git", "rev-parse"] else 2)
        with self.assertRaisesRegex(il.Problem, "already exists"):
            il.execute(self.root, p, run=run, open_pr=True, today="2026-10-01")
        self.assertFalse([c for c in calls if c[0] in ("rclone", "ia")])

    PACKET_URL = ("https://berkeleyca.gov/sites/default/files/city-council-meetings/"
                  "2026-10-27%20Agenda%20Packet%20-%20Council%20-%20WEB.pdf")

    def packet_issue(self):
        body = (ISSUE_BODY.replace("Presentation on Potential Ballot Measures", "Agenda Packet, October 27, 2026")
                .replace("2026-05-19", "2026-10-27")
                .replace("https://berkeleyca.gov/sites/default/files/2026-05/presentation.pdf", self.PACKET_URL)
                .replace("sources/ballot_2026", "agenda-packets"))
        return self.issue(body)

    def write_catalog(self):
        path = os.path.join(self.root, pi.CITY_MEETINGS)
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as f:
            json.dump({"entries": [{"meeting": "city-council-2026-10-27", "date": "2026-10-27", "label": "regular",
                                    "city": {"agenda_packet": self.PACKET_URL, "annotated_agenda": None}}]}, f)

    def test_agenda_packet_is_a_blob_whatever_its_size(self):
        self.write_catalog()
        p = il.plan(self.root, self.packet_issue(), tempfile.mkdtemp(), fetch=self.fake_fetch(1000),
                    today="2026-10-28")
        e = p["entry"]
        self.assertEqual(e["id"], "bcc-2026-10-27-regular-agenda-packet")
        self.assertEqual((e["doc_type"], e["meeting_date"], e["meeting_label"]),
                         ("agenda_packet", "2026-10-27", "regular"))
        self.assertIsNone(e["file"])
        self.assertEqual(p["place"]["l1"], "L1/agenda-packets")
        self.assertIn("2026-10-27", " ".join(il.ia_metadata(e)))

    @unittest.skipUnless(HAVE_PDFTOTEXT, "pdftotext not installed")
    def test_packet_upload_writes_text_and_survives_an_unindexable_packet(self):
        self.write_catalog()
        staging = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, staging)
        p = il.plan(self.root, self.packet_issue(), staging, fetch=self.fake_fetch(len(TEXT_PDF), TEXT_PDF),
                    today="2026-10-28")

        def run(cmd, check=True, cwd=None, **kw):
            if drive_about(cmd):
                return drive_about(cmd)
            if cmd[:2] == ["rclone", "copyto"] and cmd[2].startswith(il.REMOTE):
                shutil.copy(p["staged"], cmd[3])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            files = il.execute(self.root, p, run=run, today="2026-10-28")
        self.assertIn("manifest/bcc-2026-10-27-regular-agenda-packet-text.json", files)
        self.assertIn("no packet item index", out.getvalue())
        self.assertEqual([x for x in self.validate_errors() if "not verified" not in x], [])

    def test_agenda_packet_needs_its_meeting(self):
        with self.assertRaisesRegex(il.Problem, "meeting_date"):
            il.plan(self.root, self.packet_issue(), tempfile.mkdtemp(), fetch=self.fake_fetch(1000))
        p = il.plan(self.root, self.packet_issue(), tempfile.mkdtemp(), fetch=self.fake_fetch(1000),
                    meeting={"meeting_date": "2026-10-27", "meeting_label": "regular"})
        self.assertEqual(p["entry"]["id"], "bcc-2026-10-27-regular-agenda-packet")

    def test_pull_request_branch_starts_from_origin_main(self):
        staging = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, staging)
        p = il.plan(self.root, self.issue(), staging, fetch=self.fake_fetch(BIG), today="2026-10-01")
        calls = []

        def run(cmd, check=True, cwd=None, capture_output=False, **kw):
            if drive_about(cmd):
                return drive_about(cmd)
            calls.append(cmd)
            if cmd[:2] == ["rclone", "copyto"] and cmd[2].startswith(il.REMOTE):
                shutil.copy(p["staged"], cmd[3])
            return subprocess.CompletedProcess(cmd, 1 if cmd[0] == "git" and capture_output else 0)
        with contextlib.redirect_stdout(io.StringIO()):
            il.execute(self.root, p, run=run, open_pr=True, today="2026-10-01")
        switch = [c for c in calls if c[:2] == ["git", "switch"]][0]
        self.assertEqual(switch[-1], "origin/main")
        self.assertIn("--draft", [c for c in calls if c[:3] == ["gh", "pr", "create"]][0])
        self.assertIn(["git", "fetch", "origin", "main"], calls)


if __name__ == "__main__":
    unittest.main()
