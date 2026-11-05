"""Tests for tools/wayback.sh against a local stub of the Wayback Machine. Standard library
only, no network.

  python3 -m unittest discover -s tests -v
"""
import base64, hashlib, http.server, json, os, shutil, subprocess, tempfile, threading, unittest
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SCRIPT = os.path.join(REPO, "tools", "wayback.sh")

DOC = b"%PDF-1.4 the archived document"
OTHER = b"%PDF-1.4 a later version of it"


def sha1_b32(data):
    return base64.b32encode(hashlib.sha1(data).digest()).decode()


class Stub(http.server.BaseHTTPRequestHandler):
    """CDX listing, raw captures (/web/<ts>id_/<url>) and Save Page Now (/save/<url>)."""
    captures = {}   # source url -> [(timestamp, bytes)], oldest first
    on_save = {}    # source url -> bytes a save captures

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path == "/cdx/search/cdx":
            url = urllib.parse.parse_qs(query)["url"][0]
            rows = [["timestamp", "digest"]] + [[ts, sha1_b32(b)] for ts, b in self.captures.get(url, [])]
            return self.reply(json.dumps(rows if len(rows) > 1 else []).encode())
        if path.startswith("/web/"):
            ts, _, url = path[len("/web/"):].partition("id_/")
            for t, b in self.captures.get(url, []):
                if t == ts:
                    return self.reply(b)
            return self.reply(b"", 404)
        if path.startswith("/save/"):
            url = path[len("/save/"):]
            if url in self.on_save:
                self.captures.setdefault(url, []).append(("20261105120000", self.on_save[url]))
            return self.reply(b"saved")
        self.reply(b"", 404)

    def reply(self, body, code=200):
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class TestWayback(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "tools"))
        os.makedirs(os.path.join(self.root, "manifest"))
        shutil.copy(SCRIPT, os.path.join(self.root, "tools", "wayback.sh"))
        with open(os.path.join(self.root, "tools", "validate.py"), "w") as f:
            f.write('print("validated")\n')
        Stub.captures, Stub.on_save = {}, {}
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Stub)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.root)

    def entry(self, eid, url, data=DOC, in_git=True, **fields):
        e = {"id": eid, "layer": "L0", "sha256": hashlib.sha256(data).hexdigest(),
             "file": f"L0/sources/x/{eid}.pdf" if in_git else None,
             "source_url": url, "wayback_url": None, **fields}
        if in_git:
            os.makedirs(os.path.join(self.root, "L0/sources/x"), exist_ok=True)
            with open(os.path.join(self.root, e["file"]), "wb") as f:
                f.write(data)
        with open(os.path.join(self.root, "manifest", eid + ".json"), "w") as f:
            json.dump(e, f, indent=2, ensure_ascii=False)
            f.write("\n")

    def run_script(self, *args):
        env = dict(os.environ, WAYBACK_BASE=f"http://127.0.0.1:{self.server.server_port}")
        p = subprocess.run(["bash", os.path.join(self.root, "tools", "wayback.sh"), "--delay", "0", *args],
                           capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        return {line.split("\t")[0]: line.split("\t")[1:] for line in p.stdout.splitlines() if "\t" in line}

    def wayback(self, eid):
        with open(os.path.join(self.root, "manifest", eid + ".json")) as f:
            return json.load(f)["wayback_url"]

    def test_records_newest_matching_capture_only_with_write(self):
        url = "https://berkeleyca.gov/sites/default/files/a%20b.pdf"
        Stub.captures[url] = [("20220101000000", DOC), ("20250101000000", OTHER)]
        self.entry("a", url)
        out = self.run_script()
        self.assertEqual(out["a"], ["found", f"https://web.archive.org/web/20220101000000/{url}"])
        self.assertIsNone(self.wayback("a"))
        out = self.run_script("--write")
        self.assertEqual(out["a"][0], "recorded")
        self.assertEqual(self.wayback("a"), f"https://web.archive.org/web/20220101000000/{url}")

    def test_entry_without_file_is_checked_by_download(self):
        url = "https://example.org/big.pdf"
        Stub.captures[url] = [("20240101000000", DOC)]
        self.entry("blob", url, in_git=False)
        self.assertEqual(self.run_script("--write")["blob"][0], "recorded")

    def test_mismatch_is_never_recorded(self):
        url = "https://example.org/changed.pdf"
        Stub.captures[url] = [("20240101000000", OTHER)]
        self.entry("m", url)
        self.assertEqual(self.run_script("--write")["m"][0], "mismatch")
        self.assertIsNone(self.wayback("m"))

    def test_save_when_no_capture(self):
        url = "https://example.org/new.pdf"
        Stub.on_save[url] = DOC
        self.entry("n", url)
        self.assertEqual(self.run_script("--write")["n"][0], "none")
        out = self.run_script("--save", "--write")
        self.assertEqual(out["n"], ["saved", f"https://web.archive.org/web/20261105120000/{url}"])
        self.assertEqual(self.wayback("n"), f"https://web.archive.org/web/20261105120000/{url}")

    def test_selection(self):
        self.entry("has", "https://example.org/h.pdf", wayback_url="https://web.archive.org/web/20200101000000/x")
        self.entry("nourl", None)
        self.entry("one", "https://example.org/1.pdf")
        self.entry("two", "https://example.org/2.pdf")
        self.assertEqual(sorted(self.run_script()), ["one", "two"])
        self.assertEqual(sorted(self.run_script("--limit", "1")), ["one"])
        self.assertEqual(sorted(self.run_script("two")), ["two"])


if __name__ == "__main__":
    unittest.main()
