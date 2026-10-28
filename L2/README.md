# L2 — structured extractions

Machine-readable data parsed from L0/L1 documents. Each dataset is built by a tool in
`tools/l2/` from a recipe in `tools/l2/recipes/`, and `<tool> <recipe> --check` rebuilds it
byte for byte.

| Directory | Dataset | Tool |
|---|---|---|
| `campaign_finance/` | NetFile campaign-finance transactions, contributor names hashed | `tools/l2/netfile_transactions.py` |
| `agenda-packets/` | For each Council agenda packet, its items and the packet pages holding each item's material | `tools/l2/packet_items.py` |

An L2 dataset is added only together with its parser, a validator, and tests, all in this
repository and run by CI. Each record points back to the L0/L1 document and location it was
parsed from. See the root README for the policy, and INFORMATION_ARCHITECTURE.md for how a
packet index relates to its meeting.
