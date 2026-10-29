#!/usr/bin/env python3
"""Take the inbox files from a fork's pull request, and nothing else.

The inbox workflow for forks (.github/workflows/inbox-fork.yml) runs with write permission, so it
must never run code from the pull request. It checks out this repository's main branch, fetches
the pull request's commits as data, and calls this tool to copy across only the contribution
files: plain files directly in inbox/. Everything after that (process_inbox.py, validate.py, the
tests) is this repository's own code.

  python3 tools/fork_inbox.py --base HEAD --head <pull request head> [--apply] [--summary-file F]

Refuses, and copies nothing, if the pull request changes anything outside inbox/, deletes or
renames a file, touches inbox/README.md, adds a symbolic link, a submodule or a subfolder, or
adds a file over 25 MB.

The workflow's second job, which can push, receives the processed result as a patch from the
first and checks what it staged before committing anything:

  python3 tools/fork_inbox.py --check-staged

Refuses unless every staged change adds a plain file under L0/, L1/, T0/ or manifest/, none
over 25 MB. Standard library only.
"""
import argparse, os, re, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAX_IN_GIT = 25 * 1024 * 1024
NAME_RE = re.compile(r"^inbox/[A-Za-z0-9][A-Za-z0-9._-]*$")
KEEP = {"inbox/README.md", "inbox/.gitkeep"}
FILE_MODES = {"100644", "100755"}


def git(root, *args):
    return subprocess.run(["git", "-C", root, *args], check=True, capture_output=True).stdout


def changes(root, base, head):
    """(status, new mode, blob, path) for each path the head changes since its merge base."""
    mb = git(root, "merge-base", base, head).decode().strip()
    raw = git(root, "diff", "--raw", "-z", "--no-renames", "--no-abbrev", mb, head).decode()
    parts = raw.split("\0")
    out = []
    i = 0
    while i < len(parts) - 1:
        meta = parts[i].lstrip(":").split()
        out.append((meta[4], meta[1], meta[3], parts[i + 1]))
        i += 2
    return out


def check(root, base, head, max_bytes=MAX_IN_GIT):
    """The inbox paths to copy, and the reasons to refuse (empty when all is well)."""
    paths, errors = [], []
    for status, mode, blob, path in changes(root, base, head):
        if status == "D":
            errors.append(f"`{path}` is deleted. A pull request from a fork may only add files to `inbox/`.")
        elif path in KEEP:
            errors.append(f"`{path}` is changed. Leave it as it is; the maintainer looks after it.")
        elif not NAME_RE.match(path):
            errors.append(f"`{path}` is outside `inbox/`, in a subfolder, or has an unusual name. "
                          "Put each file directly in `inbox/`, named with letters, digits, `.`, `-` and `_`.")
        elif mode not in FILE_MODES:
            errors.append(f"`{path}` is not an ordinary file (a link or a submodule).")
        else:
            size = int(git(root, "cat-file", "-s", blob))
            if size > max_bytes:
                errors.append(f"`{path}` is {size:,} bytes, over 25 MB. Remove it from the pull request and "
                              "offer it through a \"Large file\" Issue instead.")
            else:
                paths.append(path)
    if not paths and not errors:
        errors.append("The pull request adds no files to `inbox/`.")
    return paths, errors


def apply(root, head, paths):
    git(root, "checkout", head, "--", *paths)
    for p in paths:
        os.chmod(os.path.join(root, p), 0o644)


PROCESSED_RE = re.compile(r"^(L0|L1|T0|manifest)/[A-Za-z0-9][A-Za-z0-9._/-]*$")


def check_staged(root, max_bytes=MAX_IN_GIT):
    """Reasons to refuse the staged changes (empty when they only add archive files)."""
    raw = git(root, "diff", "--cached", "--raw", "-z", "--no-renames", "--no-abbrev").decode()
    parts = raw.split("\0")
    errors, n = [], 0
    for i in range(0, len(parts) - 1, 2):
        meta = parts[i].lstrip(":").split()
        status, mode, blob, path = meta[4], meta[1], meta[3], parts[i + 1]
        n += 1
        if status != "A":
            errors.append(f"{path}: changes an existing file (status {status})")
        elif not PROCESSED_RE.match(path) or "/../" in path or "/./" in path:
            errors.append(f"{path}: not under L0/, L1/, T0/ or manifest/")
        elif mode != "100644":
            errors.append(f"{path}: mode {mode}, not an ordinary file")
        elif int(git(root, "cat-file", "-s", blob)) > max_bytes:
            errors.append(f"{path}: over 25 MB")
    if not n:
        errors.append("nothing is staged")
    return errors


def summary(errors):
    lines = ["**Nothing was processed.** The inbox robot takes only new files placed directly in `inbox/`:", ""]
    lines += [f"- {e}" for e in errors]
    lines += ["", "Fix the pull request (push to the same branch of your fork) and the robot runs again. "
              "Anything else goes in its own pull request, which the maintainer reviews by hand."]
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", help="this repository's main, checked out")
    ap.add_argument("--head", help="the pull request's head commit, fetched")
    ap.add_argument("--check-staged", action="store_true", help="check the staged processed result instead")
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--apply", action="store_true", help="copy the inbox files into the working tree")
    ap.add_argument("--summary-file", help="on refusal, write a Markdown explanation here")
    a = ap.parse_args(argv)
    if a.check_staged:
        errors = check_staged(a.root)
        for e in errors:
            print("REFUSED", e, file=sys.stderr)
        return 1 if errors else 0
    if not (a.base and a.head):
        ap.error("pass --base and --head, or --check-staged")
    paths, errors = check(a.root, a.base, a.head)
    if errors:
        for e in errors:
            print("REFUSED", e, file=sys.stderr)
        if a.summary_file:
            with open(a.summary_file, "w") as f:
                f.write(summary(errors))
        return 1
    if a.apply:
        apply(a.root, a.head, paths)
    for p in paths:
        print("inbox file", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
