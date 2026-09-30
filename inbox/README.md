# Adding a document from GitHub's website

You need only a web browser. A robot files the document, fingerprints it, extracts its text,
and tells you in a comment whether anything needs fixing. The maintainer reviews and merges.

Read the repository's README first: it says what belongs here (public records, and clearly
labeled third-party publications; never analysis or commentary).

## Steps

1. **Make a branch.** On the repository's front page, click the branch menu (it says `main`),
   type a name such as `add-clerk-resolution-71000`, and click **Create branch**. Everything
   below happens on that branch, never on `main`.
2. **Upload the document.** Open the `inbox` folder, click **Add file → Upload files**, drag in
   the PDF, and click **Commit changes**. Give it a short name without spaces, such as
   `resolution-71000.pdf`.
   - You do not need to know the file's size. GitHub's website refuses files over 25 MB with a
     "file too large" message. If that happens, stop here and fill in a
     [Large file issue](https://github.com/ericdf/berkeley-public-records/issues/new?template=large-file.yml) instead; the maintainer
     archives large files.
3. **Add the metadata file.** In `inbox`, click **Add file → Create new file**. Name it exactly
   like the document but ending in `.json` (`resolution-71000.json`), paste the template below
   that fits, fill it in, and click **Commit changes**.
4. **Open the pull request.** Click **Compare & pull request** (or **Pull requests → New**, with
   your branch). Fill in the template: what it is and where it came from. Click **Create pull
   request**.
5. **Wait a minute for the bot's comment.** If it lists what was added, you are done; check the
   title, date and source it shows. If it lists errors, fix the `.json` file (open it, click the
   pencil, edit, commit) and the bot runs again. A red check that appears before the bot has
   finished is expected: the raw inbox files are not yet processed.

## Templates

Keep the quotes and commas exactly as shown; the last line before `}` has no comma. You may
delete optional lines (see the table below). Dates are always `YYYY-MM-DD`.

**A City document that is posted online:**

```json
{
  "layer": "L0",
  "collection": "sources/budgets",
  "title": "Title exactly as printed on the document",
  "document_date": "2026-09-12",
  "publisher": "City of Berkeley — Budget Office",
  "source_url": "https://berkeleyca.gov/...",
  "retrieved": "2026-09-30"
}
```

**A staff report or other document on a meeting agenda** (Council, a Council committee, or a
commission). No `document_date` is needed; `meeting_label` is needed for Council meetings:

```json
{
  "layer": "L0",
  "collection": "staff-reports",
  "body": "Berkeley City Council",
  "title": "Title exactly as printed on the report",
  "publisher": "City of Berkeley — Office of the City Manager",
  "meeting_date": "2026-06-16",
  "meeting_label": "regular",
  "item": "Consent Calendar Item 18",
  "source_url": "https://berkeleyca.gov/...",
  "retrieved": "2026-09-30"
}
```

**A document you got from the City Clerk or another office** (not posted online):

```json
{
  "layer": "L0",
  "collection": "clerk",
  "title": "Title exactly as printed on the document",
  "document_date": "2026-09-01",
  "publisher": "City of Berkeley — City Clerk",
  "source_note": "Requested in person from the City Clerk; received 2026-09-12."
}
```

**Something published by someone else** (a newspaper, a community group, a campaign):

```json
{
  "layer": "T0",
  "collection": "press",
  "title": "Headline as published (Publication, Author)",
  "document_date": "2025-04-25",
  "publisher": "Name of the publication, and what it is (e.g. a news organization)",
  "relevance": "The public decision or official it bears on, in neutral terms.",
  "source_url": "https://...",
  "retrieved": "2026-09-30"
}
```

## What the fields mean

| Field | |
|---|---|
| `layer` | Required. `L0`: made or published by a public body. `T0`: published by anyone else. |
| `collection` | Required. L0: `clerk`, `council-captions`, `annotated-agendas`, `staff-reports`, or `sources/<topic>` (existing topics include `sources/budgets`, `sources/audits`, `sources/acfr`, `sources/investments`, `sources/ballot_2026`, `sources/t1`, `sources/hr`, `sources/pier_ferry`). T0: `press`, `campaigns`, or another single word. |
| `title` | Required. As printed on the document. |
| `document_date` | Required, except for `staff-reports`. The date on the document. |
| `publisher` | Required for T0; recommended for L0. |
| `relevance` | T0 only, required: why it is here, not what it shows. |
| `source_url` or `source_note` | One is required. A web address, or how and when you obtained it. Never guess a URL. |
| `retrieved` | Optional: the date you downloaded it. |
| `meeting_date`, `item` | Optional: the meeting and agenda item it belongs to. `council-captions` and `annotated-agendas` also need `meeting_label` (e.g. `regular`), and captions need `notices`: the City's disclaimer, copied word for word. |
| `body`, `item`, `doc_type`, `part` | `staff-reports` only. `body`: whose agenda it is on, e.g. `Berkeley City Council`, `Planning Commission`. `item` (required): as printed, e.g. `Action Calendar Item 37`, `Item 7B`. `doc_type`: `staff_report` (the default), `presentation`, `agenda_material`, or `supplemental_agenda_material`. `part`: for anything but the report itself, a short name such as `lease-list`. `meeting_date` is required, and `meeting_label` too for Council meetings. |

Public-records releases from the City's request portal (NextRequest) do not go through the
inbox; see "Public-records requests" in the main README.

People using git on their own computer, and AI agents, follow [AGENTS.md](../AGENTS.md).
