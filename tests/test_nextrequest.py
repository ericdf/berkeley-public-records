"""Tests for the NextRequest queue and ingest tools. Standard library only, no network.

  python3 -m unittest discover -s tests -v

Fixtures in tests/fixtures/nextrequest/ are real portal responses saved on 2026-09-30
(the listing page trimmed to three requests).
"""
import copy, hashlib, json, os, shutil, sys, tempfile, unittest, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tools"))

import nextrequest_client as nr  # noqa: E402
import nextrequest_watch as watch  # noqa: E402
import ingest_nextrequest as ingest  # noqa: E402
import queue_report  # noqa: E402
import validate  # noqa: E402

FIX = os.path.join(HERE, "fixtures", "nextrequest")
FAKE_PDF = b"%PDF-1.3\n% fixture bytes, not a real document\n%%EOF\n"


def fixture(name):
    with open(os.path.join(FIX, name)) as f:
        return json.load(f)


class FakeClient(nr.Client):
    """Serves fixtures instead of the network. `extra_docs` adds public documents."""

    def __init__(self, extra_docs=None):
        super().__init__(min_interval=0)
        self.extra_docs = extra_docs or {}
        self.downloads = []

    def get(self, url, accept="application/json", use_cache=True):
        u = urllib.parse.urlparse(url)
        q = dict(urllib.parse.parse_qsl(u.query))
        page = int(q.get("page_number", 1))
        path = u.path
        if path == "/client/departments":
            return json.dumps(fixture("departments.json")).encode()
        if path == "/client/requests":
            body = fixture("list_page_1.json") if page == 1 else {"total_count": 0, "requests": []}
            return json.dumps(body).encode()
        if path == "/client/request_documents":
            rid = q["request_id"]
            if rid == "26-2872":
                body = fixture("documents_26-2872.json")
            else:
                docs = self.extra_docs.get(rid, [])
                body = {"total_documents_count": len(docs), "documents": docs}
            if page > 1:
                body["documents"] = []
            return json.dumps(body).encode()
        if path.endswith("/timeline"):
            body = fixture("timeline_26-2872.json")
            if page > 1:
                body["timeline"] = []
            return json.dumps(body).encode()
        if path == "/client/requests/26-2872":
            return json.dumps(fixture("request_26-2872.json")).encode()
        if path.startswith("/documents/") and path.endswith("/download"):
            self.downloads.append(path)
            return FAKE_PDF
        raise nr.NotPublic(url)


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.staging = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "manifest"))
        self.log = lambda *a, **k: None

    def tearDown(self):
        shutil.rmtree(self.root)
        shutil.rmtree(self.staging)

    def queue(self):
        return watch.load_queue(self.root)

    def run_watch(self, client=None):
        return watch.run(client or FakeClient(), self.root, since="2026-09-01", log=self.log)

    def decide(self, rid, status, reason="test"):
        e = self.queue()[rid]
        e["decision"] = {"status": status, "reason": reason, "decided_by": "ericdf",
                         "decided_on": "2026-09-30"}
        e["status"] = status
        watch.save_entry(self.root, e)


class TestQueueEntries(Base):
    def test_entry_creation(self):
        created, _ = self.run_watch()
        self.assertEqual(sorted(e["id"] for e in created), ["26-2872", "26-2902", "26-2920"])
        q = self.queue()
        e = q["26-2872"]
        self.assertEqual(set(validate.QUEUE_REQUIRED) - e.keys(), set())
        self.assertEqual(e["request_date"], "2026-09-24")
        self.assertEqual(e["state"], "Closed")
        self.assertTrue(e["published"])
        self.assertEqual(e["document_count"], 2)
        self.assertEqual(e["departments"], ["Fire - Fire Prevention"])
        self.assertEqual(e["url"], "https://cityofberkeleyca.nextrequest.com/requests/26-2872")
        self.assertEqual(e["status"], "candidate")
        self.assertIsNone(e["decision"])
        self.assertLessEqual(len(e["request_text_excerpt"]), watch.EXCERPT_CHARS + 2)
        self.assertEqual(q["26-2920"]["status"], "watching")  # still open
        self.assertLessEqual(len(q["26-2920"]["request_text_excerpt"]), watch.EXCERPT_CHARS + 2)
        self.assertEqual(q["26-2902"]["departments"], ["Parks, Recreation and Waterfront"])

    def test_decisions_preserved_on_rerun(self):
        self.run_watch()
        self.decide("26-2872", "rejected", "a single property's inspection history")
        self.decide("26-2920", "watching", "bid tabulation; wait for release")
        before = {k: copy.deepcopy(v["decision"]) for k, v in self.queue().items()}
        self.run_watch()
        after = self.queue()
        for rid, dec in before.items():
            self.assertEqual(after[rid]["decision"], dec)
        self.assertEqual(after["26-2872"]["status"], "rejected")
        self.assertEqual(after["26-2920"]["status"], "watching")

    def test_watching_resurfaces_when_documents_become_public(self):
        self.run_watch()
        self.decide("26-2920", "watching", "wait for release")
        doc = fixture("documents_26-2872.json")["documents"][0]
        self.run_watch(FakeClient(extra_docs={"26-2920": [doc]}))
        e = self.queue()["26-2920"]
        self.assertEqual(e["status"], "candidate")
        self.assertEqual(e["decision"]["status"], "watching")
        self.assertEqual(e["document_count"], 1)
        self.assertTrue(any("re-surfaced" in n for n in e["notes"]))
        self.assertIn("26-2920", queue_report.report(self.queue()))

    def test_report_lists_only_undecided_candidates(self):
        self.run_watch()
        out = queue_report.report(self.queue())
        self.assertIn("26-2872", out)
        self.assertNotIn("26-2920", out)  # open: watching, not yet a candidate
        self.decide("26-2872", "rejected")
        self.assertNotIn("26-2872", queue_report.report(self.queue()))

    def test_split_departments(self):
        known = [d["name"] for d in fixture("departments.json")]
        self.assertEqual(nr.split_departments("Finance, Public Works - Zero Waste", known),
                         ["Finance", "Public Works - Zero Waste"])
        self.assertEqual(nr.split_departments("Parks, Recreation and Waterfront, Finance", known),
                         ["Parks, Recreation and Waterfront", "Finance"])


class TestIngest(Base):
    def test_refuses_when_not_inducted(self):
        self.run_watch()
        with self.assertRaises(ingest.Refused):
            ingest.ingest(FakeClient(), self.root, "26-2872", "ericdf", self.staging, False, self.log)
        self.decide("26-2872", "rejected")
        with self.assertRaises(ingest.Refused):
            ingest.ingest(FakeClient(), self.root, "26-2872", "ericdf", self.staging, False, self.log)
        with self.assertRaises(ingest.Refused):
            ingest.ingest(FakeClient(), self.root, "26-0000", "ericdf", self.staging, False, self.log)
        self.assertFalse(os.path.exists(os.path.join(self.root, "L0")))

    def test_manifest_entry_shape_and_idempotence(self):
        self.run_watch()
        self.decide("26-2872", "inducted", "test")
        client = FakeClient()
        res = ingest.ingest(client, self.root, "26-2872", "ericdf", self.staging, False, self.log)
        self.assertEqual(sorted(res["added"]), ["cpra-26-2872-doc-70978065",
                                                "cpra-26-2872-doc-70978846",
                                                "cpra-26-2872-request"])
        m = ingest.load_manifest(self.root)
        for mid in res["added"]:
            e = m[mid]
            self.assertEqual(validate.REQUIRED - e.keys(), set(), mid)
            self.assertTrue(validate.ID_RE.match(e["id"]))
            self.assertEqual(e["layer"], "L0")
            path = os.path.join(self.root, e["file"])
            with open(path, "rb") as f:
                data = f.read()
            self.assertEqual(hashlib.sha256(data).hexdigest(), e["sha256"])
            self.assertEqual(len(data), e["bytes"])
            self.assertEqual(e["source_url"], "https://cityofberkeleyca.nextrequest.com/requests/26-2872")
            self.assertEqual(e["cpra"]["request_id"], "26-2872")
        d = m["cpra-26-2872-doc-70978846"]
        self.assertEqual(d["file"], "L0/cpra/26-2872/cpra-26-2872-doc-70978846.pdf")
        self.assertEqual(d["cpra"]["document_id"], 70978846)
        self.assertEqual(d["cpra"]["upload_date"], "2026-09-25")
        self.assertEqual(d["cpra"]["redacted_date"], "2026-09-25")
        self.assertEqual(d["cpra"]["department"], ["Fire - Fire Prevention"])
        self.assertIsNone(m["cpra-26-2872-doc-70978065"]["cpra"]["redacted_date"])
        self.assertEqual(len(client.downloads), 2)

        again = FakeClient()
        res2 = ingest.ingest(again, self.root, "26-2872", "ericdf", self.staging, False, self.log)
        self.assertEqual(res2["added"], [])
        self.assertEqual(again.downloads, [])

    def test_large_file_becomes_pending_blob(self):
        self.run_watch()
        self.decide("26-2872", "inducted", "test")
        old = ingest.MAX_IN_GIT
        ingest.MAX_IN_GIT = 10
        try:
            res = ingest.ingest(FakeClient(), self.root, "26-2872", "ericdf", self.staging, False, self.log)
        finally:
            ingest.MAX_IN_GIT = old
        self.assertEqual(len(res["pending_blobs"]), 2)
        m = ingest.load_manifest(self.root)
        self.assertNotIn("cpra-26-2872-doc-70978065", m)
        self.assertFalse(os.path.exists(os.path.join(self.root, "L0", "cpra", "26-2872",
                                                     "cpra-26-2872-doc-70978065.pdf")))
        with open(os.path.join(self.root, "queue", "pending_blobs.json")) as f:
            pend = json.load(f)
        self.assertEqual({p["id"] for p in pend},
                         {"cpra-26-2872-doc-70978065", "cpra-26-2872-doc-70978846"})
        self.assertIsNone(pend[0]["manifest_entry"]["file"])
        self.assertTrue(os.path.exists(pend[0]["staged_at"]))


if __name__ == "__main__":
    unittest.main()
