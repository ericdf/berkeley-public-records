# Berkeley Public Records

A shared archive of Berkeley public records: the documents themselves, text extracted from
them by a recorded method, and speaker attribution for meeting transcripts. It exists so that
people working on Berkeley civic questions — who do not share analysis, code, or conclusions —
can share the primary record, check it, and stop depending on City web links that break.

**This archive contains records, not arguments.** Nothing here scores, grades, characterizes,
or interprets anyone. Analysis belongs in each contributor's own work.

Access is by invitation. The repository is private; the documents in it are public records.

---

## Contents

The archive is organized in layers. Each layer says how far it is from what the City
published, and each file is listed in `manifest/`.

| Layer | What it holds | Authority |
|---|---|---|
| **L0 — originals** | Documents exactly as published or produced by a public body: meeting caption transcripts, agendas, annotated agendas, staff reports, budgets, audits, financial statements, records obtained by public-records request | As authoritative as the source itself. Never edited. |
| **L1 — mechanical text** | Text extracted from an L0 document by a recorded tool and command | Reproducible: running the recorded command on the L0 file yields the L1 file byte for byte. |
| **L2 — structured extractions** | Machine-readable data parsed from L0/L1, e.g. motion-level votes from annotated agendas | Only as good as its parser; each L2 dataset ships with its validator and tests. None yet. |
| **L3 — speaker attribution** | Who is speaking in each segment of a caption transcript | **Not authoritative.** See the notice below. None yet. |

### Current holdings

| Collection | Layer | Path |
|---|---|---|
| Berkeley City Council meeting captions, from December 10, 2024 | L0 PDF, L1 text | `L0/council-captions/`, `L1/council-captions/` |
| Berkeley City Council annotated agendas (the City's record of motions and votes), from December 10, 2024 | L0 PDF | `L0/annotated-agendas/` |
| City budgets, audits, financial statements and policies, investment reports, Measure T1 and 2026 ballot-measure records, labor contracts, and related reports | L0 PDF, L1 text | `L0/sources/<collection>/`, `L1/sources/<collection>/` |

Two caption transcripts (the April 16 and April 23, 2026 Budget Committee meetings) exist
only as text; their source PDFs were not retained. Their manifest entries say so.

Ten scanned documents in `L0/sources/` (the November 2026 ballot arguments, the General Fund
Reserve Policy, and a 2008 West Berkeley employment memo) have no L1 text yet; they need OCR
with a recorded command. Documents without a recorded `source_url` say how they were obtained in
`source_note`.

### Notices carried with the documents

**Caption transcripts are not the official record.** Each carries the City's own notice, for
example: *"This information provided by a Certified Realtime Reporter. The City of Berkeley
cannot certify the following text since we did not create it."* The notice is recorded in the
document's manifest entry under `notices`. The official record of what the Council decided is
the annotated agenda and the adopted minutes.

**L3 speaker attribution is not authoritative.** Every L3 file carries this notice:

> Speaker attribution in this layer is inferred, not authoritative. It was produced by software
> and volunteers, not by the City. Verify any attribution against the meeting video before
> relying on it.

---

## The manifest

Every file in `L0/` through `L3/` has exactly one entry in `manifest/<id>.json`, and every
entry points to exactly one file. The manifest is what makes the archive checkable without
trusting whoever added a file.

| Field | Meaning |
|---|---|
| `id` | Stable identifier, e.g. `bcc-2026-05-19-regular-annotated-agenda`. Never reused. |
| `layer` | `L0`, `L1`, `L2`, or `L3` |
| `doc_type`, `body`, `meeting_date`, `meeting_label`, `title` | What the document is |
| `file` | Path in this repository, or `null` if the file is stored as an external blob (below) |
| `sha256`, `bytes` | Fingerprint and size of the file. Any copy anywhere can be checked against these. |
| `source_url` | Where the public body published it, if known |
| `source_note` | Required when `source_url` is null: how the document was obtained |
| `retrieved`, `archived` | When it was downloaded (if known) and when it entered the archive |
| `wayback_url` | Internet Archive Wayback Machine capture of `source_url`, if one exists |
| `derived_from` | L1–L3: the `id` and `sha256` of the document it was derived from |
| `extraction` | L1: the tool, version, and command that produced it |
| `blob` | External copies, for files not stored in git (below) |
| `notices` | Disclaimers printed on or attached to the document, verbatim |
| `cpra` | Records obtained by public-records request: `request_id`, `document_id`, `document_url`, `upload_date`, `redacted_date`, `department` |
| `contributed_by` | GitHub username of the contributor |

`tools/validate.py` checks all of this, and the public-records queue, on every pull request.
The tools' tests run there too: `python3 -m unittest discover -s tests`.

---

## Storage

**Git (this repository on GitHub) is the archive.** It is the only place documents are added or
changed, the only place contributions are made (by pull request), and the only copy people share
from. The manifest in git defines what the archive contains.

Everything else is storage, checked against the manifest's fingerprints and never edited
directly:

- **External blobs** — files too large for git, stored in two independent places (below).
- **A backup mirror** of every git-tracked file, in the archive account's Google Drive folder
  `berkeley-public-records-mirror`, refreshed from a clean checkout of `main` by
  `tools/mirror_to_drive.sh`. It records the commit it mirrors in `MIRRORED_COMMIT.txt`. It
  exists so the archive survives the loss of GitHub access. **Do not add files to the Drive
  folders or share from them**; anything placed there is not part of the archive and is
  overwritten by the next mirror run.

## Large files

Git is the wrong place for large or bulk binary files: every clone downloads every version
forever. The rules:

- **In git:** files up to **25 MB**, and every L1–L3 file.
- **External blob:** any L0 file over 25 MB, and bulk collections (such as full meeting
  packets) regardless of size.

An external blob is stored in **at least two independent places**, and its manifest entry has
`"file": null` and a `blob` field listing them:

```json
"blob": {
  "copies": [
    {"store": "internet_archive", "item": "berkeley-public-records-<id>", "path": "<filename>"},
    {"store": "<second store>", "location": "<where>"}
  ]
}
```

1. **Internet Archive (archive.org)**, uploaded as an item named
   `berkeley-public-records-<id>`. Free, public, permanent URLs, and run by an institution
   whose purpose is preservation. **Uploads there are public**, which is appropriate only
   because these are public records.
2. **A second, independent copy** kept by a maintainer (for example cloud storage the
   maintainer controls). The Internet Archive has had multi-week outages and faces legal
   pressure; no single store is treated as permanent.

**Stores in use.** Both belong to the archive's own account, `berkeleypublicrecords@gmail.com`,
not to any contributor's personal account:

| Store | Account | `store` value in the manifest |
|---|---|---|
| Internet Archive | archive.org user `berkeleypublicrecords@gmail.com` | `internet_archive` |
| Google Drive | `berkeleypublicrecords@gmail.com`, folder `berkeley-public-records-blobs`; files must be uploaded as (and owned by) that account | `google_drive` |

Access to both accounts must be recoverable by more than one person.

The `sha256` in the manifest is the guarantee, not any store. A copy is good if and only if
its fingerprint matches. The validator checks every in-repo file's fingerprint on every pull
request; maintainers check external copies periodically.

Whenever a document has a City URL, request a Wayback Machine capture of that URL as well and
record it in `wayback_url`. The City has rebuilt its website before and broken every existing
link; the capture keeps a citation resolvable after that happens again.

---

## Public-records requests

The City of Berkeley publishes its public-records (CPRA) requests on NextRequest,
<https://cityofberkeleyca.nextrequest.com/>, often with the documents it released. Those
documents are public records and are archived here as L0 under `L0/cpra/<request id>/`, but
only after the maintainer has decided that a request belongs in the archive. Nothing is ever
inducted automatically.

### The queue

`tools/nextrequest_watch.py` pages through the portal's list of requests and writes one file
per request to `queue/nextrequest/<request id>.json`. A queue file holds **metadata only**:
the request's date, state, visibility, departments, a plain-text excerpt of the request, the
number of publicly released documents, its public URL, when it was first seen and last checked,
and a `decision`. Nothing is downloaded at this stage. Queue files are not archive documents
and have no manifest entries.

| `status` | Meaning |
|---|---|
| `candidate` | Closed and published; awaiting the maintainer's decision |
| `watching` | Still open or not published, or decided "watch": the released documents are not final |
| `inducted` | The maintainer decided it belongs in the archive; ready to ingest |
| `rejected` | The maintainer decided it does not belong in the archive |

A request decided `watching` whose released documents later become public returns to
`candidate`, with a note, so it is reviewed again. Re-running the watcher refreshes state,
visibility, departments and document count, and never changes a decision.

`rank_hints` (department and keyword matches) only sort the review list. They never include or
exclude anything.

### What is inducted

**In:** records bearing on Council decisions, budgets, contracts and procurement (including bid
tabulations, whose unit prices across bidders are procurement data), audits, capital projects,
public-safety operations, and governance.

**Out:** records about a single private party's own affairs (for example, one property's
inspection history), personnel matters, and requests closed with no released documents.

The criteria decide what enters the archive, not what a record says. A record that meets them is
inducted whatever it shows.

### Decisions and review

- **Decisions are made only by the maintainer.** A decision records `status`, `reason`,
  `decided_by` and `decided_on`; `tools/validate.py` rejects a decision without all four, or by
  anyone other than the maintainer.
- **Review is weekly, by pull request.** Each week the watcher is run on a branch and the pull
  request body carries the review list printed by `tools/queue_report.py`. The maintainer records
  decisions in the queue files on that branch and merges it.
- **Ingest follows induction, in its own pull request.** `tools/ingest_nextrequest.py <id>`
  refuses any request not decided `inducted`. It archives the request record (the request, its
  full timeline and its document index, as one JSON file) and every publicly released document,
  with manifest entries under ids such as `cpra-26-2872-request` and
  `cpra-26-2872-doc-70978065`, and L1 text (`pdftotext -layout`) for PDFs with extractable text.
  It is idempotent by document id and fingerprint. Released documents over 25 MB are not written
  to the repository; they are listed in `queue/pending_blobs.json` until two verified external
  copies exist (see "Large files").
- The portal serves each file at `/documents/<document id>/download`, which redirects to a
  short-lived signed storage URL. The manifest records the stable request URL as `source_url`
  and the document's portal page under `cpra.document_url`.

### Records released only to a requester

Records the City released only to the person who asked, and has not yet published, enter the same
queue (a queue file added by pull request, since the watcher cannot see them) and follow the same
decision. Once inducted, their manifest entries carry a `source_note`
naming the request and the release date. When the City later publishes them, the entry gains the
public URL by pull request. The file never changes.

---

## Contributing

All writes go through pull requests. This section is the policy.

### Who can write

- Access is by invitation of the maintainer. Contributors open pull requests; **only the
  maintainer merges.**
- **Nobody pushes directly to `main`**, including the maintainer. Every change, however small,
  is a pull request. (Branch protection is not available on this repository's plan, so this is
  a rule, not a setting.)
- Contributors may use software agents to prepare pull requests and reviews. The human who
  opens the pull request is responsible for everything in it. A review comment from any agent
  is input, never approval.

### What may be added

- **L0:** documents produced or published by a public body. Sources on official domains
  (e.g. `berkeleyca.gov`) need `source_url`. Records obtained by public-records request, or
  from a public body by other means, need a `source_note` saying how and when. Documents from
  private parties are not L0.
- **L1:** only text produced by a tool and command recorded in `extraction`, so anyone can
  reproduce it. Hand-corrected text is not L1.
- **L2:** a structured dataset may be added only with the parser, a validator, and tests, all
  in this repository, run by CI.
- **L3:** see the attribution rules below.

### What may never be added

Analysis, commentary, scores, grades, characterizations, rankings, or selections made to
support an argument. If a file would read differently depending on who assembled it, it does
not belong here.

### Rules by layer

**L0 is add-only.** A document is never edited, replaced, or deleted to correct it. If a
public body issues a corrected or revised version, add it as a new document with its own `id`,
and note the relationship in both manifest entries.

**Manifest metadata may be corrected by pull request; files may not.** A wrong title, date,
source URL or note in a manifest entry is fixed in the entry. The `file`, `sha256` and `bytes`
of an existing entry never change, because the file never changes.

**L1 is regenerated, never edited.** If a better extraction tool or command is adopted, the new
text is a new L1 document (new `id`, `derived_from` the same L0), so work that cited the old
text can still reproduce it.

**L3 attribution rules:**

- Each L3 file is JSON, one per caption transcript, pinned to the `sha256` of the L1 text it
  describes. If that text is regenerated, the attribution must be re-anchored, not silently
  reused.
- Each segment records `start` and `end` (elapsed time), `speaker`, `attribution_level`, and
  `basis`:
  - `verified` — a person checked it against the meeting video or another independent
    authoritative source. **Requires `video_timestamp`, `verified_by`, and `verified_on`**, so
    anyone can re-check.
  - `cued` — an explicit cue in the transcript (the chair names the speaker; the speaker
    self-identifies), not independently checked.
  - `inferred` — diarization, carry-over from the previous turn, or context.
- The levels are exclusive. A strong cue is still `cued` until someone checks the video.
- **Speaker identifiers** come from `registry/speakers.json`: officeholders as `off-<name>`,
  staff by role as `staff:<role>`, and members of the public as `public:<name-as-recorded>`.
- **A speaker is attributed by the name as it appears in the public record** — as read by the
  clerk or chair, or as printed in the packet. Nothing is added from outside the record.
  Public comment is public; the archive indexes the record and does not enrich it.
- **Corrections require evidence.** A pull request changing a segment's speaker cites a video
  timestamp or a transcript cue. A segment confirmed against video is upgraded to `verified`
  for everyone.

### Pull requests

- One collection or one change per pull request.
- `tools/validate.py` must pass (CI runs it).
- Describe where each new document came from. For L3, describe how segments were attributed.

### Licensing

Public records are public. The manifest, L1 text, L2 data, and L3 attribution in this
repository are dedicated to the public domain under
[CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/). No rights are claimed in the
underlying public documents.

### Removal requests

A document is removed only if a public body did not in fact make it public (for example, it was
released in error and withdrawn), or if a court orders it. Removal is a pull request by the
maintainer that records the reason in the manifest entry, which is kept.
