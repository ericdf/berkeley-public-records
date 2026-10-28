# Information architecture

How the archive's records relate to each other: what a document is, which meeting and item it
belongs to, where it came from, and where its bytes are stored. README.md is the policy and
governs where this file disagrees with it; AGENTS.md is the operating procedure. This file is
the map both of them assume.

## Four questions every record answers

| Question | Answered by |
|---|---|
| What is it? | `manifest/<id>.json`: `layer`, `doc_type`, `body`, `meeting_date`, `meeting_label`, `item`, `title` |
| Where did it come from? | `source_url` (published) or `source_note` (obtained another way), `retrieved` |
| Where are its bytes? | `file` (in git) or `blob` (external copies, `file: null`) |
| What is it derived from? | `derived_from` (L1–L3), with the parent's `id` and `sha256` |

The manifest is the only list of what the archive holds. `catalog/` lists what the City
publishes and `queue/` what is pending; neither is a holding.

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
  label separates them. For Council meetings the City's own key is the meeting slug (below).
- **Item**: one numbered agenda item, recorded in `item` as printed (`Item 7`,
  `Action Calendar Item 37`, `Item 6D`).
- **Document**: one file, one manifest entry, one fingerprint. Never edited. A revised or
  supplemental version is a new document with its own id.

## Collections and ids

| Collection | Holds | Id pattern | Example |
|---|---|---|---|
| `council-captions` | Council caption transcripts | `bcc-<date>-<label>-captions` | `bcc-2026-09-29-regular-captions` |
| `annotated-agendas` | Council annotated agendas | `bcc-<date>-<label>-annotated-agenda` | `bcc-2026-07-28-regular-annotated-agenda` |
| `agenda-packets` | Council agenda packets, as external blobs | `bcc-<date>-<label>-agenda-packet` | `bcc-2026-10-27-regular-agenda-packet` |
| `staff-reports` | Individually published agenda item material for Council, committees and commissions | `<body>-<date>[-<label>]-item-<n>-<part>` | `bcc-2026-06-16-item-37-staff-report` |
| `clerk` | Records obtained from the City Clerk not covered above | `clerk-<description>` | `clerk-2024-candidate-roster-…` |
| `sources/<topic>` | Budgets, audits, financial statements, policies and other City documents | `<topic>-<date>-<description>` | `waterfront-…-san-francisco-bay-plan` |
| `cpra/<request>` | Public-records releases published on NextRequest (none ingested yet) | `cpra-<request>-doc-<doc id>` | `cpra-26-2872-doc-70978065` |
| `T0/<collection>` | Published third-party documents | as assigned | |

L1 text mirrors its L0 parent's path under `L1/` and takes the parent id plus `-text`. An
agenda packet's item index is `L2/agenda-packets/<packet id>-items.json`.

Known inconsistencies in existing entries, left as they are until a deliberate metadata pass:
caption and annotated-agenda entries carry `collection: null`; staff-report entries carry
`staff_reports` (underscore) while the path and inbox value are `staff-reports`; and
`meeting_label` is mixed-case (`regular` and `Regular` both occur). Compare labels
case-insensitively, as `tools/meeting_map.py` does.

## A Council meeting's document set

The City's system of record for which documents belong to which Council meeting is the agenda
listing at <https://berkeleyca.gov/your-government/city-council/city-council-agendas>, which
lists every meeting from January 2021. Each meeting is one row with its own page, whose slug
identifies the meeting: `city-council-2026-10-27`, with `-0`, `-1` appended for further
meetings on the same date. The City labels each meeting `Regular`, `Special`,
`Closed Session` or `Canceled`.

| Role | City column | Source | When it appears | Archived as | Storage |
|---|---|---|---|---|---|
| Agenda | Agenda (HTML) | City page | Before the meeting | Not archived separately; see the supersession rules | — |
| Agenda packet | Agenda Packet (PDF) | City page | Before the meeting | `agenda-packets` | External blob, whatever its size |
| Packet item index | — | Built from the packet's L1 text | When the packet is archived | L2, `L2/agenda-packets/` | Git |
| Annotated agenda | Annotated Agenda (PDF) | City page | After the meeting | `annotated-agendas` | Git |
| Caption transcript | — | Emailed by the City Clerk Department | After the meeting | `council-captions` | Git |
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
  material; check before skipping it. If it carries item material it is archived in
  `agenda-packets`.
- **Revised and supplemental material is never superseded.** Each version is its own document
  (`doc_type: supplemental_agenda_material`, `part: supp-2`, `revised`), because what the
  Council had in front of it, and when, is part of the record.

### Cardinality

A meeting usually has at most one of each role, with these exceptions:

- **One caption transcript can cover two meetings.** When a special meeting runs directly into
  a regular one, the captioning is one file: `meeting_label: "Special and Regular"`, id
  `bcc-<date>-special-and-regular-captions`. It belongs to both meetings.
- **Caption variants.** A meeting may have more than one captioning source (`-zoom`, `-vtt` in
  the id). Each is its own document.
- **One recording per meeting** in the Granicus catalog, though a combined session can be a
  single clip.
- **Closed sessions and canceled meetings** are listed by the City but have no documents here.

### Join keys

| Key | Joins |
|---|---|
| City meeting slug (`city-council-2026-10-27`) | The City's row: agenda, packet, annotated agenda, recording links |
| `source_url` | A manifest entry to the City row that links to it (packets and annotated agendas) |
| `meeting_date` + `meeting_label` | Manifest entries of every document of that meeting, compared word by word |
| MP3/MP4 URL | The City's row to the Granicus catalog entry (the URLs are identical) |
| `meeting_date` + `item` | Item material to its meeting |

Caption transcripts have no City URL and join only by `meeting_date` and `meeting_label`.

### The City meeting catalog

`catalog/city-council-agendas/meetings.json`, written by `tools/catalog_city_agendas.py`, which
reads the City listing and downloads no documents. One entry per City row, keyed by the meeting
slug:

```json
{
 "meeting": "city-council-2026-10-27",
 "name": "City Council 2026-10-27 - Regular",
 "date": "2026-10-27", "label": "regular",
 "listed_time": "10/27/2026 - 6:00 pm", "start_utc": "2026-10-28T01:00:00Z",
 "page_url": "https://berkeleyca.gov/your-government/city-council/city-council-agendas/city-council-2026-10-27",
 "city": {"agenda_html": "…", "agenda_packet": "…", "annotated_agenda": null,
          "video": "…", "mp3": null, "mp4": null},
 "first_listed": "2026-10-28", "last_listed": "2026-10-28"
}
```

The catalog records what the City lists and nothing about what the archive holds. Entries are
never deleted; a meeting the City stops listing keeps its `last_listed` date, and a link the
City changes is visible in the catalog's history.

### The meeting map

`tools/meeting_map.py` joins the City catalog, the manifest and the Granicus catalog, and
reports for every listed meeting the archive ids filling each role: `agenda_packet`,
`annotated_agenda`, `captions`, `items`, `packet_index`, and the Granicus clip of its
recording. It is computed on demand and never stored, so it cannot fall out of date with the
manifest.

- `tools/meeting_map.py` prints the gaps: what the City lists that the archive does not hold,
  from the earliest archived caption transcript or annotated agenda (or `--since`).
- `tools/meeting_map.py --meeting city-council-2026-10-27` prints one meeting's record.
- `tools/meeting_map.py --json FILE` writes the whole map.

It also lists archived meeting documents that match no listed meeting: material from before
2021, and committee and work-session material, are expected there.

### The packet item index

An agenda packet holds every item's material in one file, so item-level addressing comes from
an index rather than from splitting it. Splitting the packet would produce files the City never
published, which are not L0.

`tools/l2/packet_items.py` builds the index from the packet's L1 text, which stays in git while
the packet itself is an external blob, so the index can be rebuilt and checked by anyone. For
each agenda item it records the number and heading as printed, the agenda section, and the range
of packet pages holding its material (`null` if the packet has none). Every page is accounted
for in `spans`: the agenda, each item, and `other` pages such as blank separators and the
communications notice. Pages are the form-feed-delimited pages of the L1 text, which are the
PDF's pages. Citations take the form `bcc-2026-10-27-regular-agenda-packet`, pp. 187–250.

The parser reads the agenda's numbered items up to its Communications heading, and finds each
item's material by the City's item tab: a page whose first line is "Page 1 of N" and whose next
line is the item number. It refuses a packet that does not fit (a tab for an item the agenda
does not list, overlapping ranges, a range past the end), so a change in the City's format
stops the build instead of producing a wrong index. On the 526-page October 27, 2026 packet it
indexes all 35 items.

`tools/ingest_large.py` builds the index when it archives a packet. An individually published
item PDF is archived in `staff-reports` in addition only when a document needs its own id.

## Provenance channels

| Channel | Layer | Recorded as |
|---|---|---|
| Published on a City website | L0 | `source_url`, `retrieved`, `wayback_url` |
| Emailed by the City Clerk Department (every caption transcript) | L0 | `source_note` naming the department, that it arrived by email, and the date received when known |
| Provided in person or on request by the Clerk or another office | L0 | `source_note` naming the office, how, and the date received |
| Published on NextRequest after induction | L0 | `source_url` (the request), `cpra` block |
| Published by a third party | T0 | `publisher`, `relevance`, `source_url` or `source_note` |

The provenance of a document is how this archive obtained it, which is not always how the City
distributes it to others.

## Storage and growth

| Content | Where | Why |
|---|---|---|
| L0 and T0 files up to 25 MB, except bulk collections | Git | Fingerprinted and shared with every clone |
| L0 over 25 MB, and bulk collections (agenda packets, recordings) regardless of size | Internet Archive plus Google Drive, `file: null` | Git history is permanent; every clone downloads every byte forever |
| All L1, L2, L3 | Git | Small, and needed for search and reproducibility |

The growth rule follows from this. A Council agenda packet runs to tens of megabytes and the
Council meets a few dozen times a year, so packets in git would add on the order of a gigabyte
a year; splitting them into items would not help, since the 25 MB limit is per file and the
bytes are the same. Packets as external blobs add only their L1 text (the 526-page October 27,
2026 packet extracts to 1.7 MB) and their item index.

## Tools

| Tool | Reads | Writes |
|---|---|---|
| `tools/catalog_city_agendas.py` | The City agenda listing | `catalog/city-council-agendas/meetings.json` |
| `tools/catalog_granicus.py` | The Granicus listing | `catalog/granicus/city-council.json` |
| `tools/meeting_map.py` | Both catalogs and the manifest | Nothing (prints, or `--json FILE`) |
| `tools/process_inbox.py` | `inbox/` | L0/T0, L1, manifest; fills a meeting's date and label from the City catalog by `source_url` |
| `tools/ingest_large.py` | A Large file Issue | Blob copies, manifest, L1, and for a packet its item index |
| `tools/l2/packet_items.py` | A packet's L1 text | `L2/agenda-packets/`, its recipe and manifest entry |
