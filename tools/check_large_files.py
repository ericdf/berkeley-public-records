#!/usr/bin/env python3
"""Refuse files over 25 MB before they enter git history.

A file over 25 MB, once committed and pushed, stays in the repository's history for good,
even if a later commit deletes it and the pull request is never merged. So the check runs
before the commit (the pre-commit hook) and on every push (the large-files workflow).

  python3 tools/check_large_files.py --staged          files staged for commit (the hook)
  python3 tools/check_large_files.py --range A..B      files added or changed in commits A..B

Exit status 1, naming each file, if any is over 25 MB. Standard library only.
"""
import argparse, subprocess, sys

MAX_IN_GIT = 25 * 1024 * 1024


def git(*args, cwd=None):
    return subprocess.run(["git", *args], capture_output=True, check=True, cwd=cwd).stdout


def blob_sizes(entries, cwd=None):
    """entries: [(path, object name)] -> [(path, bytes)]"""
    out = []
    for path, obj in entries:
        out.append((path, int(git("cat-file", "-s", obj, cwd=cwd))))
    return out


def staged(cwd=None):
    """Files added or modified in the index: [(path, bytes)]."""
    names = git("diff", "--cached", "--name-only", "--diff-filter=AMCR", "-z", cwd=cwd)
    paths = [p for p in names.decode().split("\0") if p]
    return blob_sizes([(p, ":" + p) for p in paths], cwd=cwd)


def in_range(rev_range, cwd=None):
    """Every file version added or modified by any commit in rev_range: [(path, bytes)].
    Checks each commit, not only the end state, because deleting a file in a later commit
    does not remove it from history."""
    out, seen = [], set()
    for commit in git("rev-list", rev_range, cwd=cwd).decode().split():
        raw = git("diff-tree", "--root", "-r", "--no-commit-id", "--diff-filter=AMCR", "-z",
                  commit, cwd=cwd).decode().split("\0")
        # -z raw format: ":<modes> <shas> <status>" then path(s)
        i = 0
        while i < len(raw) - 1:
            meta = raw[i]
            if not meta.startswith(":"):
                i += 1
                continue
            fields = meta[1:].split()
            status, obj = fields[4], fields[3]
            path = raw[i + 2] if status[0] in "CR" else raw[i + 1]
            i += 3 if status[0] in "CR" else 2
            if obj not in seen:
                seen.add(obj)
                out.append((path, obj))
    return blob_sizes(out, cwd=cwd)


def oversized(sizes, limit=MAX_IN_GIT):
    return [(p, n) for p, n in sizes if n > limit]


def message(big, pushed=False):
    lines = ["", "STOP: these files are over 25 MB and cannot go into the repository:", ""]
    lines += [f"  {p}  ({n / 1048576:.1f} MB)" for p, n in big]
    lines += ["", "A file over 25 MB stays in the repository's history forever once pushed, even if",
              "it is deleted afterwards. Large files are stored outside git by the maintainer.", ""]
    if pushed:
        lines += ["What to do: delete this branch on GitHub (do not open a pull request from it),",
                  "then start a new branch without the large file."]
    else:
        lines += ["What to do: unstage it (git restore --staged <file>) and do not commit it."]
    lines += ["Then open a \"Large file\" Issue with the document's City URL or a Google Drive link;",
              "the maintainer archives it (README, \"Large files\").", ""]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--staged", action="store_true")
    g.add_argument("--range", dest="rev_range")
    a = ap.parse_args(argv)
    sizes = staged() if a.staged else in_range(a.rev_range)
    big = oversized(sizes)
    if big:
        print(message(big, pushed=not a.staged), file=sys.stderr)
        return 1
    if not a.staged:
        print(f"{len(sizes)} file version(s) checked; none over 25 MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
