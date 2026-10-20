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

        def run(cmd, check=True, cwd=None):
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
        self.assertEqual(self.validate_errors(), [])

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

    def test_pull_request_branch_starts_from_origin_main(self):
        staging = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, staging)
        p = il.plan(self.root, self.issue(), staging, fetch=self.fake_fetch(BIG), today="2026-10-01")
        calls = []

        def run(cmd, check=True, cwd=None, capture_output=False):
            calls.append(cmd)
            if cmd[:2] == ["rclone", "copyto"] and cmd[2].startswith(il.REMOTE):
                shutil.copy(p["staged"], cmd[3])
            return subprocess.CompletedProcess(cmd, 1 if cmd[0] == "git" and capture_output else 0)
        with contextlib.redirect_stdout(io.StringIO()):
            il.execute(self.root, p, run=run, open_pr=True, today="2026-10-01")
        switch = [c for c in calls if c[:2] == ["git", "switch"]][0]
        self.assertEqual(switch[-1], "origin/main")
        self.assertIn(["git", "fetch", "origin", "main"], calls)


if __name__ == "__main__":
    unittest.main()
