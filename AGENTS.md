# AGENTS.md — operating manual for software agents

You are contributing to an archive of Berkeley public records. README.md is the contribution
policy and binds you; this file is how to follow it. When they disagree, README.md wins: stop
and tell the human.

## Non-negotiables

- **Records, not analysis.** Add documents, mechanical text, and metadata. Never add analysis,
  commentary, scores, grades, characterizations, rankings, or selections made to support an
  argument, in any file, including manifest fields and commit messages. The bar on selections
  applies to compiled or curated documents (anthologies, excerpt sets, summaries), not to which
  records get added: a record is a record whatever the reason it was chosen, and contributors add
  the records their work needs.
- **Never push to `main`. Never merge.** Work on a branch; open a pull request. Only the
  maintainer merges. Your review comments are input, never approval.
- **Pull requests only.** Never email documents or send them anywhere else.
- **Provenance is required.** Every document has a `source_url` (where it is published) or a
  `source_note` (how and when it was obtained). Never invent a URL, date, title, publisher or
  source. If you do not know, ask the human.
- **Public-records portal releases (NextRequest):** archive only what the City has published on
  the portal, and only after the maintainer inducts the request. Never archive records released
  privately to a requester.
- **Documents from the City Clerk or another office** are welcome as L0 with a `source_note`
  naming the office, how it was provided, and the date received.
- **T0 is separate.** Anything not made or published by a public body is T0, never L0, with a
  `publisher` and a neutral `relevance`: why it is here, not what it shows.
- **L3 speaker attribution:** `verified` requires `video_timestamp`, `verified_by` and
  `verified_on`; otherwise use `cued` or `inferred`. Name a speaker exactly as the record names
  them (as read by the clerk or chair, or printed in the packet). Add nothing from outside the
  record.
- **Nothing over 25 MB is ever committed.** Once pushed, it stays in history forever.

## Procedure

1. **Install the hook, always, first:** `sh tools/install-hooks.sh` (refuses commits of files
   over 25 MB).
2. `git switch main && git pull --ff-only`, then branch: `git switch -c add/<short-description>`
   (for example `add/clerk-resolution-71000`).
3. For each document, put the file in `inbox/<name>.pdf` and its metadata in
   `inbox/<name>.json`. Templates and field meanings: `inbox/README.md`. One collection per
   pull request.
4. Check sizes before committing: `ls -l inbox/`. Anything over 25 MB (26214400 bytes): stop,
   remove it, and follow "File over 25 MB" below.
5. Run `python3 tools/process_inbox.py --contributor <GitHub username of the human>`
   (`gh api user --jq .login` gives it). Fix every error it prints and rerun; it changes nothing
   until the whole inbox is valid. It needs `pdftotext` (poppler) for L1 text.
6. Run `python3 tools/validate.py` and `python3 -m unittest discover -s tests`. Both must pass.
7. Review `git status` and `git diff --stat`: only the new L0/T0 file(s), L1 text, and
   `manifest/` entries. Commit with a message that says what was added and where it came from.
8. `git push -u origin <branch>`, then open the pull request with
   `gh pr create --repo ericdf/berkeley-public-records --base main` using the sections of
   `.github/pull_request_template.md` (What this adds or changes; Where it came from; Checklist).

If the human prefers, skip step 5: push the raw inbox files and the inbox GitHub Action
processes them on the pull request and comments with the result.

## Cases

| Case | Do |
|---|---|
| City document with a URL | L0; `collection` `sources/<topic>` (or `council-captions`, `annotated-agendas`); `source_url`, `retrieved`. Request a Wayback capture of the URL and tell the human; recording `wayback_url` is a manifest edit by PR. |
| Obtained from the Clerk or another office | L0; `collection` `clerk` or `sources/<topic>`; `source_note`, e.g. "Requested in person from the City Clerk; received 2026-09-12." |
| Public-records request | Do not archive the released files. If the request is not yet in `queue/nextrequest/`, add it there by PR (it stays `watching` until the City publishes it). The maintainer decides; `tools/ingest_nextrequest.py` ingests after induction. |
| Third-party publication | T0; `collection` such as `press`, `campaigns`; `publisher`, neutral `relevance`, `source_url` or `source_note`. |
| Scanned PDF with no text | Process it anyway. The tool records "no text layer; needs OCR". Do not OCR it yourself unless the human asks for a recorded OCR command. |
| File over 25 MB | Do not commit it. Tell the human to open a "Large file" Issue (the form takes the City URL, or a Google Drive link shared with `berkeleypublicrecords@gmail.com`). The maintainer runs the large-file procedure below. |
| Council captions | `council-captions` needs `meeting_date`, `meeting_label`, and `notices` with the City's disclaimer copied verbatim. |

## Large-file procedure (maintainer, or you at the maintainer's direction)

1. `python3 tools/ingest_large.py <issue number> [--collection sources/<topic>]` — the default is
   a dry run: it downloads to `~/berkeley-public-records-pending-blobs`, fingerprints, refuses
   duplicates, and prints the plan. It uploads nothing and writes nothing to the repository.
2. Show the human the plan. **Internet Archive uploads are public and permanent.** Run
   `--upload` only after the maintainer explicitly approves that upload, every time; an earlier
   approval never carries over.
3. `python3 tools/ingest_large.py <n> --upload [--open-pr]` copies the file to Google Drive
   (verified by download), uploads it to the Internet Archive, writes the manifest entry
   (`file: null`, both copies, the Internet Archive copy unverified until public) and L1 text,
   and opens a PR that closes the Issue (or prints the commands).
4. Once the Internet Archive item is public, download it, check its sha256, and record
   `verified` in a follow-up PR.

## Never

- Edit, replace or delete an existing file in `L0/` or `T0/`; a corrected version is a new
  document. Never change an existing entry's `file`, `sha256` or `bytes`.
- Hand-edit L1 text. L1 is regenerated by a recorded command, as a new document.
- Add analysis or commentary anywhere.
- Fabricate a URL, date, title, publisher or source; invent or guess a speaker attribution.
- Merge a pull request, push to `main`, or pass `--no-verify` to skip the hook.
- Upload to the Internet Archive or Google Drive (except `ingest_large.py --upload` with the
  maintainer's explicit approval for that file), or add files to the Drive folders by hand.

## Report back to the human

Say, briefly: the PR URL; each id added, its layer and collection; its source (`source_url` or
`source_note`); whether L1 text was generated or it needs OCR; anything refused and why; and
any question only the human or maintainer can answer. Do not claim anything was verified that
you did not check.
