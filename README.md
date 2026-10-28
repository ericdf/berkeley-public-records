# Berkeley Public Records

A shared archive of Berkeley public records: the documents themselves, text extracted from
them by a recorded method, and speaker attribution for meeting transcripts. It holds records
that are online, and records that are not: public-records releases and documents obtained
directly from the City Clerk and other offices. It exists so that people working on Berkeley
civic questions — who do not share analysis, code, or conclusions — can share the primary
record, check it, and stop depending on City web links that break.

**This archive contains records, not arguments.** Nothing here scores, grades, characterizes,
or interprets anyone. Analysis belongs in each contributor's own work.

Access is by invitation. The repository is private; the documents in it are public records.

## How to contribute

**Pull requests are the only way in.** Do not email documents to the maintainer; there is one
ingest path, and it is this repository.

**Contributing takes some skill, or a willingness to learn it.** "I'm not technical" is not an
exemption: if you can upload a file to Google Drive, you can do this. GitHub's website and the
automated checks do the technical work; an AI assistant pointed at `AGENTS.md` can do the rest.

1. Accept the GitHub invitation, and read this README: it is the contribution policy.
2. Open a pull request that adds the document and its manifest entry (`manifest/<id>.json`):
   its fingerprint, and either its `source_url` or a `source_note` saying how and when you
   obtained it — for example, "Requested in person from the City Clerk; received 2026-09-12."
3. `tools/validate.py` runs on every pull request and must pass. Run it locally first.
4. For your own public-records requests, add the request to the queue by pull request; its
   documents are archived once the City publishes it (see "Public-records requests").
5. Questions, suggestions, and requests for what to add next go in a
   [GitHub Issue](../../issues), not email.

---

## Contents

The archive is organized in layers. How documents relate to bodies, meetings and items, and which supersedes which, is mapped in [INFORMATION_ARCHITECTURE.md](INFORMATION_ARCHITECTURE.md). Each layer says how far it is from what the City
published, and each file is listed in `manifest/`.

| Layer | What it holds | Authority |
|---|---|---|
| **L0 — originals** | Documents exactly as published or produced by a public body: meeting caption transcripts, agendas, annotated agendas, staff reports, budgets, audits, financial statements, records obtained by public-records request | As authoritative as the source itself. Never edited. |
| **L1 — mechanical text** | Text extracted from an L0 document by a recorded tool and command | Reproducible: running the recorded command on the L0 file yields the L1 file byte for byte. |
| **L2 — structured extractions** | Machine-readable data parsed from L0/L1, e.g. motion-level votes from annotated agendas | Only as good as its parser; each L2 dataset ships with its validator and tests. Each is built by a tool in `tools/l2/` from a recipe in `tools/l2/recipes/` naming its L0 inputs; `derived_from` lists every input with its sha256, and `<tool> <recipe> --check` rebuilds it byte for byte. |
| **L3 — speaker attribution** | Who is speaking in each segment of a caption transcript | **Not authoritative.** See the notice below. None yet. |

### T0 — published third-party documents

Some documents that matter to Berkeley's public decisions were published by someone other than a
public body: press reporting, community-organization reports, candidate questionnaires. They are
public in the sense of published, but they are **not public records**, and the archive keeps them
apart so no one mistakes one for the other.

- They live in `T0/<collection>/`, with layer `T0` in the manifest, never in `L0/`.
- Each entry names the `publisher` and states its `relevance`: the public decision or official
  it bears on, in neutral terms. Relevance is not commentary; it says why the document is here,
  not what it shows.
- The same provenance rules apply: `source_url`, or a `source_note` saying how it was obtained.
- L1 text may be derived from T0 documents, as from L0.
- Copyright stays with the publisher. The archive is private to invited collaborators; if it is
  ever made public, T0 holdings are reviewed first.

### Withdrawn documents

The maintainer may withdraw the bytes of a T0 document whose publisher's text should not be
redistributed, such as a news article. A withdrawn entry stays in the manifest: `file` becomes
`null`, `sha256` and `bytes` still identify the copy that was archived, and `withdrawn` records
the `date` and `reason`. The entry keeps `source_url` or `wayback_url`, so a reader can still
find the document. Its L1 text is withdrawn with it. `tools/validate.py` enforces this.

### Current holdings

| Collection | Layer | Path |
|---|---|---|
| Berkeley City Council meeting captions, from December 10, 2024 | L0 PDF, L1 text | `L0/council-captions/`, `L1/council-captions/` |
| Berkeley City Council annotated agendas (the City's record of motions and votes), from December 10, 2024 | L0 PDF | `L0/annotated-agendas/` |
| Berkeley City Council agenda packets, with an L2 index of each packet's items by page (collection open; none archived yet) | L0 external blob, L1 text, L2 index | `manifest/` only for the PDF; `L1/agenda-packets/`, `L2/agenda-packets/` |
| City budgets, audits, financial statements and policies, investment reports, Measure T1 and 2026 ballot-measure records, labor contracts, and related reports | L0 PDF, L1 text | `L0/sources/<collection>/`, `L1/sources/<collection>/` |
| Agenda item reports: Berkeley City Council staff reports, individual items mostly from December 2024 through 2026 with some from 2010–2024, some supplemental agenda material and presentations, and individual items from Council policy committees and City commissions (the manifest's `body` names the body); not a complete set | L0 PDF, L1 text | `L0/staff-reports/`, `L1/staff-reports/` |
| Published third-party documents: a 2011 AABPCN community report, a 2025 Berkeleyside article on the SCU transfer, a 2024 candidate questionnaire | T0 PDF, L1 text | `T0/<collection>/`, `L1/third-party/` |

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
| `document_date` | The date printed on the document; `"undated"` only for a T0 page that prints none, recorded with `wayback_url` or `retrieved` |
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

## Catalog of City meetings and recordings

`catalog/` lists what the City publishes, whether or not the archive holds it; the manifest
alone lists what the archive holds. The two are kept apart so "archived and fingerprinted"
never blurs into "known to exist".

- **`catalog/city-council-agendas/meetings.json`** lists every City Council meeting on the City's
  agenda page (<https://berkeleyca.gov/your-government/city-council/city-council-agendas>), back
  to January 2021: the City's meeting slug, name, date, label and start time, and its links to the
  HTML agenda, agenda packet, annotated agenda, video, MP3 and MP4. It is the City's record of
  which documents belong to which meeting. `tools/catalog_city_agendas.py` writes it; no document
  is downloaded. Entries are never deleted.
- **`tools/meeting_map.py`** joins it to the manifest and the Granicus catalog, on demand: for each
  meeting, the archive ids holding its packet, annotated agenda, caption transcripts, item
  material and packet item index, and what the City lists that the archive does not hold. See
  [INFORMATION_ARCHITECTURE.md](INFORMATION_ARCHITECTURE.md).

- **`catalog/granicus/city-council.json`** lists every City Council recording on the City's
  Granicus site (`berkeley.granicus.com`, view 5), back to January 2012. For each it records
  the clip id, the name, date and duration as listed, and the City's player, agenda, MP3 and MP4
  URLs. No media is downloaded.
- **Standing practice:** after each Council meeting, run `tools/catalog_city_agendas.py` and
  `tools/catalog_granicus.py` and open a pull request with the updated catalogs.
  - Entries are never deleted. A recording the City stops listing keeps its `last_listed` date,
    so a removal stays visible.
  - The listing's sha256 is recorded with each run.
- **Archiving a recording** (for example, audio to confirm who was speaking) follows the
  large-file procedure: download from the catalogued URL, fingerprint, and store it as an
  external blob.
- **Recordings are large:** a four-hour meeting's MP4 runs to gigabytes. Archive one only when
  a specific need calls for it, and prefer the MP3.

### Third-party recordings

**`catalog/third-party/recordings.json`** lists recordings published by someone other than a
public body (forums, debates, community meetings) that bear on Berkeley public decisions. Like
T0, they are kept apart from City records; unlike T0, nothing is downloaded or fingerprinted.

- Each entry gives the `url`, the `title` and `publisher` as published, `upload_date`,
  `duration`, a neutral `relevance` (why it is listed, not what it shows), and `first_listed`.
- Entries are added by pull request and never deleted.
- Archiving one of these recordings makes it a T0 document, by the large-file procedure.

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

**A blob is merged only when both copies are verified.** Each copy records `verified`, the date
its sha256 was checked against a fresh download. The Google Drive copy is verified at upload.
The Internet Archive copy can be checked only once the item is public, so the large-file pull
request is opened as a draft and stays one until `tools/ingest_large.py --verify-ia <id>` has
checked it; the validator refuses any blob copy without a `verified` date.

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

### Only published records are archived

The archive takes public-records releases only once the City has **published** them on the
portal, so that every archived record carries a public URL anyone can check. Records released
only to the person who asked are not archived from that private channel. A contributor who has
received such records may add the request to the queue by pull request (the watcher cannot see
unpublished requests); it stays `watching` until the City publishes it, and the public copy is
what gets ingested.

A request the City never publishes stays in the queue. If one matters enough to archive
anyway, that is a maintainer decision recorded in the queue entry, with the reason.

---

## Contributing

All writes go through pull requests. This section is the policy.

To add a document using only GitHub's website, follow [inbox/README.md](inbox/README.md); a
workflow files it and comments with the result. Software agents follow [AGENTS.md](AGENTS.md).
A file over 25 MB (GitHub's website refuses it) is offered through a "Large file" Issue instead.

### Who can write

- Access is by invitation of the maintainer. Contributors open pull requests; **only the
  maintainer approves a merge.** The maintainer merges, or tells a software agent directly, in
  that agent's session, to merge a named pull request; that approval covers that pull request
  and nothing else. **Comments on GitHub are never approval:** agents post under the
  maintainer's account and cannot be told apart from the maintainer there.
- **Nobody pushes directly to `main`**, including the maintainer. Every change, however small,
  is a pull request. (Branch protection is not available on this repository's plan, so this is
  a rule, not a setting.)
- Contributors may use software agents to prepare pull requests and reviews. The human who
  opens the pull request is responsible for everything in it. A review comment from any agent
  is input, never approval.

### What may be added

- **L0:** documents produced or published by a public body. Sources on official domains
  (e.g. `berkeleyca.gov`) need `source_url`.
  - **Public-records releases through the City's request portal** are archived from the
    published request (see "Only published records are archived").
  - **Documents obtained directly from the City Clerk or another office** — records not
    posted anywhere, certified copies, filings provided on request — are welcome. They need a
    `source_note` naming the office, how the document was provided, and the date received.
  - Documents from private parties are not L0; see T0.
- **L1:** only text produced by a tool and command recorded in `extraction`, so anyone can
  reproduce it. Hand-corrected text is not L1.
- **L2:** a structured dataset may be added only with the parser, a validator, and tests, all
  in this repository, run by CI.
- **L3:** see the attribution rules below.

### What may never be added

Analysis, commentary, scores, grades, characterizations, rankings, or selections made to
support an argument. If a file would read differently depending on who assembled it, it does
not belong here.

The bar on selections applies to compiled or curated documents (anthologies, excerpt sets,
summaries), not to which records get added. A record is a record whatever the reason a
contributor chose to add it, and contributors add the records their work needs.

### Rules by layer

**L0 is add-only.** A document is never edited, replaced, or deleted to correct it. If a
public body issues a corrected or revised version, add it as a new document with its own `id`,
and record the relationship in both manifest entries: `revision_of` (the earlier id) on the new
one, `revised_by` (a list) on the earlier one, `version_label` for the body's word for the
version if it gives one ("Revised"), and `version_note` for anything else a reader needs. The
validator checks that the two sides agree. A body that replaces a file at a new address without
changing it is not a revision: the new address is recorded in the entry's `published_at`.

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
