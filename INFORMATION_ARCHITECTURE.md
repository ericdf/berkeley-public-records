# Information architecture

How the archive's records relate to each other: what a document is, which meeting and item it
belongs to, where it came from, and where its bytes are stored. README.md is the policy and
governs where this file disagrees with it; AGENTS.md is the operating procedure. This file is
the map both of them assume.

Sections marked **Planned** describe structure that has been designed but not built. Until it
is built, nothing in the archive depends on it.

## Four questions every record answers

| Question | Answered by |
|---|---|
| What is it? | `manifest/<id>.json`: `layer`, `doc_type`, `body`, `meeting_date`, `meeting_label`, `item`, `title` |
| Where did it come from? | `source_url` (published) or `source_note` (obtained another way), `retrieved` |
| Where are its bytes? | `file` (in git) or `blob` (external copies, `file: null`) |
| What is it derived from? | `derived_from` (L1–L3), with the parent's `id` and `sha256` |

The manifest is the only list of what the archive holds. `catalog/` and `queue/` list things
that exist or are pending; they are never holdings.

## Entities

```
Body ──< Meeting ──< Item
           │           │
           └──< Document >──┘        (a document belongs to a meeting, and optionally to an item)
                  │
                  └──< Derived document (L1 text, L2 data, L3 attribution)
```

- **Body**: the public body whose agenda it is: `Berkeley City Council`, a Council committee
  (`Agenda & Rules Committee`, `Budget & Finance Committee`), or a commission. Recorded in
  `body`.
- **Meeting**: one session of a body. Identified in the manifest by `meeting_date` plus
  `meeting_label` (`regular`, `special`, `work session`, or a combined label such as
  `Special and Regular` or `Special JPFA and Regular`). Several meetings can share a date; the
  label separates them.
- **Item**: one numbered agenda item, recorded in `item` as printed (`Item 7`,
  `Action Calendar Item 37`, `Item 6D`).
- **Document**: one file, one manifest entry, one fingerprint. Never edited. A revised or
  supplemental version is a new document with its own id.

## Collections and ids

| Collection | Holds | Id pattern | Example |
|---|---|---|---|
| `council-captions` | Council caption transcripts | `bcc-<date>-<label>-captions` | `bcc-2026-09-29-regular-captions` |
| `annotated-agendas` | Council annotated agendas | `bcc-<date>-<label>-annotated-agenda` | `bcc-2026-07-28-regular-annotated-agenda` |
| `staff-reports` | Agenda item material for Council, committees and commissions | `<body>-<date>[-<label>]-item-<n>-<part>` | `bcc-2026-06-16-item-37-staff-report` |
| `clerk` | Records obtained from the City Clerk not covered above | `clerk-<description>` | `clerk-2024-candidate-roster-…` |
| `sources/<topic>` | Budgets, audits, financial statements, policies and other City documents | `<topic>-<date>-<description>` | `waterfront-…-san-francisco-bay-plan` |
| `cpra/<request>` | Public-records releases published on NextRequest (none ingested yet) | `cpra-<request>-doc-<doc id>` | `cpra-26-2872-doc-70978065` |
| `T0/<collection>` | Published third-party documents | as assigned | |

L1 text mirrors its L0 parent's path under `L1/` and takes the parent id plus `-text`.

Known inconsistencies in existing entries, left as they are until a deliberate metadata pass:
caption and annotated-agenda entries carry `collection: null`; staff-report entries carry
`staff_reports` (underscore) while the path and inbox value are `staff-reports`; and
`meeting_label` is mixed-case (`regular` and `Regular` both occur). Compare labels
case-insensitively.

## A Council meeting's document set

The City's system of record for which documents belong to which Council meeting is the agenda
listing at <https://berkeleyca.gov/your-government/city-council/city-council-agendas>. Each
meeting is one row, and each row has its own page, whose slug identifies the meeting:
`city-council-2026-10-27`, with `-0`, `-1` appended for further meetings on the same date.

| Role | City column | Source | When it appears | Archived as | Storage |
|---|---|---|---|---|---|
| Agenda | Agenda (HTML) | City page | Before the meeting | Not archived separately; see the supersession rules | — |
| Agenda packet | Agenda Packet (PDF) | City page | Before the meeting | `staff-reports`, `doc_type: agenda_material` | External blob (bulk collection, and usually over 25 MB) |
| Annotated agenda | Annotated Agenda (PDF) | City page | After the meeting | `annotated-agendas` | Git |
| Caption transcript | — | Emailed by the City Clerk's office | After the meeting | `council-captions` | Git |
| Recording | Video, Download (MP3, MP4) | Granicus | After the meeting | Catalogued in `catalog/granicus/city-council.json`; archived only on need | External blob |
| Individual item material | — (linked from the agenda) | City page | Before the meeting; supplemental and revised material up to and during it | `staff-reports` | Git |
| Adopted minutes | — | City Clerk | Weeks later | Not yet collected | — |

**The official record of what the Council decided is the annotated agenda and the adopted
minutes.** Caption transcripts carry the City's notice that it cannot certify them.

### Supersession rules

- **The annotated agenda supersedes the agenda.** It reproduces the agenda and adds the action
  taken on each item. If the annotated agenda is available at ingest time, the agenda is not
  archived.
- **Nothing supersedes the agenda packet.** The annotated agenda carries the agenda, not the
  staff reports, presentations and attachments the packet compiles. The packet is the only
  complete source of a meeting's item material as it stood when the agenda was published.
- **For a special meeting the City lists a PDF titled "Special Agenda" in the packet column.**
  It is superseded by the special meeting's annotated agenda only if it contains no item
  material; check before skipping it.
- **Revised and supplemental material is never superseded.** Each version is its own document
  (`doc_type: supplemental_agenda_material`, `part: supp-2`, `revised`), because what the
  Council had in front of it, and when, is part of the record.

### Cardinality

A meeting usually has at most one of each role, with these exceptions:

- **One caption transcript can cover two meetings.** When a special meeting runs directly into
  a regular one, the City's captioning is one file: `meeting_label: "Special and Regular"`,
  id `bcc-<date>-special-and-regular-captions`.
- **Caption variants.** A meeting may have more than one captioning source (`-zoom`, `-vtt` in
  the id). Each is its own document.
- **One recording per meeting** in the Granicus catalog, though a combined session can be a
  single clip.

### Join keys

| Key | Joins |
|---|---|
| City meeting slug (`city-council-2026-10-27`) | The City's row: agenda, packet, annotated agenda, recording links |
| `meeting_date` + `meeting_label` | Manifest entries of every document of that meeting |
| MP3/MP4 URL | The City's row to the Granicus catalog entry (the URLs are identical) |
| `meeting_date` + `item` | Item material to its meeting |

Caption transcripts have no City URL and join only by `meeting_date` and `meeting_label`.

### Planned: the meeting map

`catalog/city-council-agendas/meetings.json`, built by a tool that reads the City listing and
downloads nothing, in the manner of `tools/catalog_granicus.py`. One record per City row:

```json
{
  "meeting": "city-council-2026-10-27",
  "date": "2026-10-27", "time": "18:00", "label": "Regular",
  "city": {"agenda_html": "…", "agenda_packet": "…", "annotated_agenda": null, "mp3": "…", "mp4": "…"},
  "archived": {
    "agenda_packet": "bcc-2026-10-27-agenda-packet",
    "annotated_agenda": null,
    "captions": null,
    "recording": null
  }
}
```

The `city` block is refreshed from the listing on every run. The `archived` block names the
manifest ids filling each role, so a missing document reads as `null`, not as silence. A
combined caption transcript is named in both meetings' records. Entries are never deleted; a
meeting the City stops listing keeps its `last_listed` date.

### Planned: the packet item index

An agenda packet holds every item's material in one file, so item-level addressing comes from
an index rather than from splitting it. Splitting the packet would produce files the City never
published, which are not L0.

The index is an L2 dataset built by a tool in `tools/l2/` from the packet: for each item, its
number as printed and its page range in the packet. Citations take the form
`bcc-2026-10-27-agenda-packet`, pp. 212–240. The 2026-10-27 packet has no PDF bookmarks, so the
tool parses the agenda listing and the item headings in the L1 text, where `pdftotext -layout`
keeps one form feed per page. An individually published item PDF is archived in addition only
when a document needs its own id.

## Provenance channels

| Channel | Layer | Recorded as |
|---|---|---|
| Published on a City website | L0 | `source_url`, `retrieved`, `wayback_url` |
| Emailed by the City Clerk's office (caption transcripts) | L0 | `source_note` naming the office, that it arrived by email, and the date received |
| Provided in person or on request by the Clerk or another office | L0 | `source_note` naming the office, how, and the date received |
| Published on NextRequest after induction | L0 | `source_url` (the request), `cpra` block |
| Published by a third party | T0 | `publisher`, `relevance`, `source_url` or `source_note` |

The provenance of a document is how this archive obtained it, which is not always how the City
distributes it to others. Existing caption entries record "Caption PDF published by the City of
Berkeley"; where that is not how a transcript was obtained, the `source_note` is corrected by
pull request.

## Storage and growth

| Content | Where | Why |
|---|---|---|
| L0 and T0 files up to 25 MB, except bulk collections | Git | Fingerprinted and shared with every clone |
| L0 over 25 MB, and bulk collections (agenda packets, recordings) regardless of size | Internet Archive plus Google Drive, `file: null` | Git history is permanent; every clone downloads every byte forever |
| All L1, L2, L3 | Git | Small, and needed for search and reproducibility |

The growth rule follows from this. A Council agenda packet runs to tens of megabytes and the
Council meets a few dozen times a year, so packets in git would add on the order of a gigabyte
a year; splitting them into items would not help, since the 25 MB limit is per file and the
bytes are the same. Packets as external blobs add only their L1 text (the 526-page 2026-10-27
packet extracts to 1.7 MB) and their item index.
