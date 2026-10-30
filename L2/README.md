# L2 — structured extractions

Machine-readable data parsed from L0/L1 documents. Each dataset is built by a tool in
`tools/l2/` from a recipe in `tools/l2/recipes/`, and `<tool> <recipe> --check` rebuilds it
byte for byte.

| Directory | Dataset | Tool |
|---|---|---|
| `campaign_finance/` | NetFile campaign-finance transactions, contributor names hashed | `tools/l2/netfile_transactions.py` |
| `agenda-packets/` | For each Council agenda packet, its items and the packet pages holding each item's material | `tools/l2/packet_items.py` |
| `annotated-agendas/` | For each Council annotated agenda, what the Clerk recorded: attendance, each item's From, Contact and Action text, authorship roles, co-sponsors added, motions and recorded votes, and the correspondence listed | `tools/l2/annotated_agenda.py` |

An L2 dataset is added only together with its parser, a validator, and tests, all in this
repository and run by CI. Each record points back to the L0/L1 document and location it was
parsed from. See the root README for the policy, and INFORMATION_ARCHITECTURE.md for how a
packet index relates to its meeting.

In `campaign_finance/`, `name_key` is a join key: the first 16 hex digits of the sha256 of the
lowercased contributor name, so repeat contributions by the same name can be grouped. Names
themselves are in the L0 NetFile responses the table is built from.

In `annotated-agendas/`, each record is `<annotated agenda id>-record`, read from that agenda's L1
text. The tool's docstring states each parsing rule. What a reader most needs to know:

- **It records what was printed, and nothing else.** A motion is a `substitute` only when the
  agenda says so; a failed motion followed by the motion that decided the item is `competing`.
  Whether it was a substitute is a question for the recording, not this record.
- **Votes belong to the motion they follow.** `vote: null` means no vote line was printed for
  that motion; it is never filled from a neighboring one. A Consent Calendar item has no vote of
  its own: the Council adopts the calendar in one vote. `prior_first_reading_vote` is an earlier
  meeting's vote, printed in a second-reading item's heading.
- **`all_ayes`** is "All Ayes" as printed, with no names. Who was present is in `attendance`,
  including late arrivals; a member who arrived after the vote is not thereby an aye.
- **Recusals** printed inside another list ("Absent – Kesarwani (recused ...)") are listed under
  `recused`, and `normalization_notes` says where the name was printed.
- **Authorship** is the From line as printed, split into names with the role the Clerk printed
  (`author`, `co-author`, `co-sponsor`, or `null`) and their order. `contact` is the Contact line
  as printed. `added_cosponsors` are the members the Action text records as added, which it does
  without saying when. Members are registry ids (`registry/speakers.json`); anyone else is kept
  as printed with `member: null`.
- **Correspondence** counts are the Clerk's: one per numbered entry, the "(n)" printed after a
  name, or the N of "N similarly worded form letters". Entries are as printed, including those
  submitted by officials; telling the two apart is the reader's classification.
