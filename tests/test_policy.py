"""Tests for approved source hosts, the source check, staged external blobs, and approved_by.
Standard library only, no network.

  python3 -m unittest discover -s tests -v
"""
import contextlib, io, json, os, shutil, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, HERE)

import check_sources as cs  # noqa: E402
import ingest_large as il  # noqa: E402
import process_inbox as pi  # noqa: E402
import source_hosts as sh  # noqa: E402
import validate  # noqa: E402
from test_inbox import Base, L0_CLERK, L0_URL, T0, TEXT_PDF, HAVE_PDFTOTEXT  # noqa: E402

DRIVE = "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWxYz/view?usp=sharing"


class TestSourceHosts(unittest.TestCase):
    def setUp(self):
        self.hosts = sh.load(REPO)

    def test_registry_is_well_formed(self):
        self.assertEqual(sh.problems(REPO), [])

    def test_matching(self):
        yes = ["https://berkeleyca.gov/x.pdf", "https://www.cityofberkeley.info/a.pdf",
               "http://berkeley.granicus.com/MediaPlayer.php", "https://www.berkeleyschools.net/b.pdf",
               "https://www.bcdc.ca.gov/plan.pdf", "https://www.albanyca.gov/c.pdf",
               "https://cityofberkeleyca.nextrequest.com/requests/26-1"]
        no = ["https://berkeleyca.gov.example.com/x.pdf", "https://example.org/berkeleyca.gov",
              "https://netfile.com/a.pdf", "ftp://berkeleyca.gov/x.pdf", "https://other.granicus.com/x",
              "not a url", None]
        for u in yes:
            self.assertIsNotNone(sh.approved(u, self.hosts), u)
        for u in no:
            self.assertIsNone(sh.approved(u, self.hosts), u)

    def test_problems_name_each_fault(self):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root)
        os.makedirs(os.path.join(root, "registry"))
        with open(os.path.join(root, sh.PATH), "w") as f:
            json.dump({"hosts": [{"host": "Example.org", "body": "x", "subdomains": True},
                                 {"host": "a.gov", "body": "", "subdomains": "yes"},
                                 {"host": "a.gov", "body": "A", "subdomains": False, "note": 1}]}, f)
        p = sh.problems(root)
        self.assertEqual(len(p), 5, p)


class TestCheckSources(Base):
    def add(self, mid, **kw):
        e = {"id": mid, "layer": "L0", "doc_type": "budget_document", "title": "T",
             "file": f"L0/sources/budgets/{mid}.pdf", "sha256": "a" * 64, "bytes": 10,
             "source_url": "https://berkeleyca.gov/x.pdf", "archived": "2026-10-30",
             "notices": [], "contributed_by": "contributor1"}
        e.update(kw)
        pi.write_json(os.path.join(self.root, "manifest", mid + ".json"), e)

    def test_match_mismatch_failure_and_skips(self):
        self.add("same")
        self.add("changed")
        self.add("down")
        self.add("page", file="L0/sources/budgets/page.html")
        self.add("elsewhere", source_url="https://example.org/x.pdf")
        self.add("blob", file=None)
        self.add("portal", cpra={"request_id": "26-1"})

        def fetch(url):
            return {"same": ("a" * 64, 10), "changed": ("b" * 64, 11)}[self.current]

        hosts = sh.load(REPO)
        errors, warnings, checked = [], [], []
        for mid in ("same", "changed", "down", "page", "elsewhere", "blob", "portal"):
            self.current = mid
            e, w, c = cs.check(self.root, [mid], hosts, fetch=fetch)
            errors += e; warnings += w; checked += c
        self.assertEqual(checked, ["same", "changed"])
        self.assertEqual(len(errors), 1)
        self.assertIn("changed: https://berkeleyca.gov/x.pdf now serves a different file", errors[0])
        self.assertEqual(len(warnings), 1)
        self.assertIn("down: could not download", warnings[0])


class TestStagedBlobs(Base):
    def setUp(self):
        super().setUp()
        self.staging = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.staging)

    def run_staged(self, today="2026-10-01"):
        return pi.process(self.root, "contributor1", today=today, log=lambda *a: None, staging=self.staging)

    def test_staged_document_lands_with_its_text(self):
        self.drop("big", dict(L0_URL, staged_url=DRIVE))
        r = self.run_staged()
        mid = r["added"][0]["id"]
        e = self.entry(mid)
        self.assertIsNone(e["file"])
        (copy,) = e["blob"]["copies"]
        self.assertEqual(copy, {"store": "staged", "url": DRIVE, "filename": f"{mid}.pdf",
                                "staged_by": "contributor1", "staged_on": "2026-10-01"})
        self.assertNotIn("staged_url", e)
        moved = os.path.join(self.staging, f"{mid}.pdf")
        self.assertEqual(pi.sha256_file(moved), e["sha256"])     # kept outside the repository
        self.assertEqual(self.inbox_left(), [])
        if HAVE_PDFTOTEXT:
            self.assertTrue(os.path.exists(os.path.join(self.root, "manifest", mid + "-text.json")))
        self.assertEqual(self.validate_errors(), [])
        self.assertTrue(validate.pending_preservation(e))

    def test_staging_refuses_a_non_drive_link_and_t0(self):
        self.drop("a", dict(L0_URL, staged_url="https://example.org/big.pdf"))
        with self.assertRaisesRegex(pi.Problem, "must be a Google Drive file link"):
            self.run_staged()
        os.remove(os.path.join(self.root, "inbox", "a.json"))
        os.remove(os.path.join(self.root, "inbox", "a.pdf"))
        self.drop("b", dict(T0, staged_url=DRIVE))
        with self.assertRaisesRegex(pi.Problem, "only L0 documents are staged"):
            self.run_staged()

    def test_agenda_packet_can_be_staged(self):
        self.drop("packet", {"layer": "L0", "collection": "agenda-packets", "title": "Agenda Packet",
                             "document_date": "2026-10-27", "meeting_date": "2026-10-27",
                             "meeting_label": "regular", "source_url": "https://berkeleyca.gov/p.pdf",
                             "staged_url": DRIVE})
        r = self.run_staged()
        self.assertEqual(r["added"][0]["id"], "bcc-2026-10-27-regular-agenda-packet")
        self.assertIsNone(self.entry("bcc-2026-10-27-regular-agenda-packet")["file"])

    def test_blob_copy_rules(self):
        base = {"id": "x", "layer": "L0", "file": None}
        ok_staged = dict(base, blob={"copies": [{"store": "staged", "url": DRIVE, "staged_by": "a",
                                                 "staged_on": "2026-10-30"}]})
        ok_archive = dict(base, blob={"copies": [{"store": "google_drive", "location": "d", "verified": "2026-10-30"},
                                                 {"store": "internet_archive", "item": "i", "verified": None}]})
        self.assertEqual(validate.blob_errors(ok_staged), [])
        self.assertEqual(validate.blob_errors(ok_archive), [])
        self.assertTrue(validate.pending_preservation(ok_archive))
        only_unverified_ia = dict(base, blob={"copies": [{"store": "internet_archive", "item": "i"}]})
        self.assertIn("external blob needs a staged copy or a verified archive copy",
                      validate.blob_errors(only_unverified_ia))
        self.assertTrue(validate.blob_errors(dict(base, blob={"copies": []})))
        self.assertTrue(any("not verified" in m for m in validate.blob_errors(
            dict(base, blob={"copies": [{"store": "google_drive", "location": "d"}]}))))
        self.assertTrue(any("only L0" in m for m in validate.blob_errors(dict(ok_staged, layer="T0"))))
        self.assertTrue(any("store must be one of" in m for m in validate.blob_errors(
            dict(base, blob={"copies": [{"store": "dropbox"}]}))))

    def test_in_git_file_may_carry_an_internet_archive_copy(self):
        e = {"id": "x", "layer": "L0", "file": "L0/clerk/x.pdf",
             "blob": {"copies": [{"store": "internet_archive", "item": "i", "verified": "2026-10-30"}]}}
        self.assertEqual(validate.in_git_copy_errors(e), [])
        self.assertTrue(validate.in_git_copy_errors(dict(e, layer="T0")))
        e2 = dict(e, blob={"copies": [{"store": "google_drive", "location": "d", "verified": "2026-10-30"}]})
        self.assertTrue(validate.in_git_copy_errors(e2))


class TestApproval(Base):
    def test_no_source_l0_needs_approved_by_from_the_policy_date(self):
        self.drop("clerk", L0_CLERK)
        with self.assertRaisesRegex(pi.Problem, "needs \"approved_by\""):
            pi.process(self.root, "contributor1", today="2026-10-30", log=lambda *a: None)
        os.remove(os.path.join(self.root, "inbox", "clerk.json"))
        self.drop("clerk", dict(L0_CLERK, approved_by="contributor1"))
        r = pi.process(self.root, "contributor1", today="2026-10-30", log=lambda *a: None)
        e = self.entry(r["added"][0]["id"])
        self.assertEqual(e["approved_by"], "contributor1")
        self.assertEqual(self.validate_errors(), [])

    def test_approval_rules(self):
        clerk = {"id": "x", "layer": "L0", "source_url": None, "archived": "2026-10-30"}
        self.assertTrue(validate.approval_errors(clerk))
        self.assertEqual(validate.approval_errors(dict(clerk, archived="2026-10-29")), [])
        self.assertEqual(validate.approval_errors(dict(clerk, approved_by="ericdf")), [])
        self.assertTrue(validate.approval_errors(dict(clerk, approved_by="not a login")))
        self.assertTrue(validate.approval_errors(dict(clerk, approved_by="ericdf", source_url="https://berkeleyca.gov/x")))
        self.assertTrue(validate.approval_errors(dict(clerk, approved_by="ericdf", layer="T0")))


class TestPreserve(Base):
    """ingest_large.py --preserve, the daily archive job, with every external command faked."""
    def setUp(self):
        super().setUp()
        self.staging = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.staging)
        self.calls = []
        shutil.copy(os.path.join(REPO, sh.PATH), os.path.join(self.root, sh.PATH))

    def run_cmd(self, data):
        def run(cmd, check=True, cwd=None, **kw):
            if cmd[:2] == ["rclone", "about"]:
                return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"free": 10 * 2**30, "total": 15 * 2**30}))
            self.calls.append(cmd)
            if cmd[:3] == ["rclone", "backend", "copyid"]:            # fetch the staged file
                with open(os.path.join(cmd[5], "shared.pdf"), "wb") as f:
                    f.write(data)
            elif cmd[:2] == ["rclone", "copyto"] and cmd[2].startswith(il.REMOTE):
                with open(cmd[3], "wb") as f:                         # the verification download
                    f.write(data)
            return subprocess.CompletedProcess(cmd, 0, stdout="")
        return run

    def stage(self, meta):
        self.drop("big", dict(meta, staged_url=DRIVE))
        r = pi.process(self.root, "contributor1", today="2026-10-01", log=lambda *a: None, staging=self.staging)
        return r["added"][0]["id"]

    def source(self, data):
        def fetch(url, dest):
            path = os.path.join(dest, "source.pdf")
            with open(path, "wb") as f:
                f.write(data)
            return path
        return fetch

    def test_staged_blob_becomes_the_archives_copies(self):
        mid = self.stage(L0_URL)
        self.assertEqual(il.preserve_pending(self.root), [(mid, "staged")])
        with contextlib.redirect_stdout(io.StringIO()):
            files, problems = il.preserve(self.root, self.staging, run=self.run_cmd(TEXT_PDF),
                                          fetch=self.source(TEXT_PDF), today="2026-10-02")
        self.assertEqual(problems, [])
        self.assertIn(f"manifest/{mid}.json", files)
        ia, drive = self.entry(mid)["blob"]["copies"]
        self.assertEqual((ia["store"], ia["verified"]), ("internet_archive", None))
        self.assertEqual((drive["store"], drive["verified"]), ("google_drive", "2026-10-02"))
        self.assertEqual([c[0] for c in self.calls], ["rclone", "rclone", "rclone", "ia"])
        self.assertEqual(il.preserve_pending(self.root), [])
        self.assertEqual(self.validate_errors(), [])

    def test_mismatches_upload_nothing(self):
        mid = self.stage(L0_URL)
        other = TEXT_PDF + b"\n% altered\n"
        for run_data, src_data, why in ((other, TEXT_PDF, "staged file"), (TEXT_PDF, other, "serves a different file")):
            self.calls = []
            with contextlib.redirect_stdout(io.StringIO()):
                files, problems = il.preserve(self.root, self.staging, run=self.run_cmd(run_data),
                                              fetch=self.source(src_data), today="2026-10-02")
            self.assertEqual(files, [])
            self.assertTrue(any(why in x for x in problems), problems)
            self.assertNotIn("ia", [c[0] for c in self.calls])
            self.assertEqual(il.preserve_pending(self.root), [(mid, "staged")])

    def test_unreachable_source_defers(self):
        self.stage(L0_URL)

        def down(url, dest):
            raise OSError("connection refused")
        with contextlib.redirect_stdout(io.StringIO()):
            files, problems = il.preserve(self.root, self.staging, run=self.run_cmd(TEXT_PDF), fetch=down)
        self.assertEqual(files, [])
        self.assertIn("will try again", problems[0])

    def test_no_source_record_gets_an_internet_archive_copy(self):
        self.drop("clerk", dict(L0_CLERK, approved_by="contributor1"))
        r = pi.process(self.root, "contributor1", today="2026-10-30", log=lambda *a: None)
        mid = r["added"][0]["id"]
        self.assertEqual(il.preserve_pending(self.root), [(mid, "no-source")])
        with contextlib.redirect_stdout(io.StringIO()):
            files, problems = il.preserve(self.root, self.staging, run=self.run_cmd(b""), today="2026-10-31")
        self.assertEqual(problems, [])
        (ia,) = self.entry(mid)["blob"]["copies"]
        self.assertEqual(ia["store"], "internet_archive")
        self.assertEqual([c[0] for c in self.calls], ["ia"])
        self.assertEqual(il.preserve_pending(self.root), [])
        self.assertEqual(self.validate_errors(), [])


if __name__ == "__main__":
    unittest.main()
