"""Tests for tools/fork_inbox.py, the gate between a fork's pull request and the inbox workflow
that runs with write permission. Standard library only, no network.

  python3 -m unittest discover -s tests -v
"""
import os, shutil, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tools"))

import fork_inbox as fi  # noqa: E402


class ForkInboxTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.git("init", "-q", "-b", "main")
        self.write("inbox/README.md", "how to\n")
        self.write("tools/validate.py", "print('ok')\n")
        self.commit("base")
        self.git("switch", "-q", "-c", "contribution")

    def tearDown(self):
        shutil.rmtree(self.root)

    def git(self, *args):
        return subprocess.run(["git", "-C", self.root, *args], check=True, capture_output=True).stdout.decode()

    def write(self, rel, text):
        p = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(text)

    def commit(self, msg):
        self.git("add", "-A")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.org", "commit", "-q", "-m", msg)

    def contribute(self, **kw):
        self.commit("contribution")
        head = self.git("rev-parse", "HEAD").strip()
        self.git("switch", "-q", "main")
        return fi.check(self.root, "main", head, **kw), head

    def test_inbox_files_are_taken(self):
        self.write("inbox/report.pdf", "%PDF\n")
        self.write("inbox/report.json", "{}\n")
        (paths, errors), head = self.contribute()
        self.assertEqual(errors, [])
        self.assertEqual(sorted(paths), ["inbox/report.json", "inbox/report.pdf"])
        fi.apply(self.root, head, paths)
        self.assertTrue(os.path.exists(os.path.join(self.root, "inbox", "report.pdf")))
        self.assertEqual(os.stat(os.path.join(self.root, "inbox", "report.pdf")).st_mode & 0o777, 0o644)

    def test_a_change_outside_the_inbox_refuses_everything(self):
        self.write("inbox/report.pdf", "%PDF\n")
        self.write("tools/validate.py", "import os; os.system('curl evil')\n")
        (paths, errors), _ = self.contribute()
        self.assertTrue(any("tools/validate.py" in e for e in errors), errors)

    def test_workflow_and_subfolder_are_refused(self):
        self.write(".github/workflows/x.yml", "on: push\n")
        self.write("inbox/sub/report.pdf", "%PDF\n")
        (paths, errors), _ = self.contribute()
        self.assertEqual(len(errors), 2, errors)

    def test_symlink_is_refused(self):
        os.makedirs(os.path.join(self.root, "inbox"), exist_ok=True)
        os.symlink("/etc/passwd", os.path.join(self.root, "inbox", "report.pdf"))
        (paths, errors), _ = self.contribute()
        self.assertTrue(any("not an ordinary file" in e for e in errors), errors)

    def test_readme_change_and_deletion_are_refused(self):
        self.write("inbox/README.md", "changed\n")
        os.remove(os.path.join(self.root, "tools", "validate.py"))
        (paths, errors), _ = self.contribute()
        self.assertTrue(any("inbox/README.md" in e for e in errors), errors)
        self.assertTrue(any("deleted" in e for e in errors), errors)

    def test_large_file_is_refused(self):
        self.write("inbox/big.pdf", "x" * 100)
        (paths, errors), _ = self.contribute(max_bytes=50)
        self.assertTrue(any("over 25 MB" in e for e in errors), errors)

    def test_empty_contribution_is_refused(self):
        self.write("inbox/.hidden", "x")
        (paths, errors), _ = self.contribute()
        self.assertTrue(errors)

    def test_main_moved_on_since_the_fork(self):
        self.write("inbox/report.pdf", "%PDF\n")
        self.commit("contribution")
        head = self.git("rev-parse", "HEAD").strip()
        self.git("switch", "-q", "main")
        self.write("L0/other.pdf", "%PDF\n")
        self.commit("someone else's merge")
        paths, errors = fi.check(self.root, "main", head)
        self.assertEqual((paths, errors), (["inbox/report.pdf"], []))


if __name__ == "__main__":
    unittest.main()
