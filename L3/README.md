# L3 — transcript structure and speaker attribution (not authoritative)

> Speaker attribution in this layer is inferred, not authoritative. It was produced by software
> and volunteers, not by the City. Verify any attribution against the meeting video before
> relying on it.

## Speaker attribution

One JSON file per caption transcript, listed in `manifest/` with `derived_from` pointing to the
L1 text. None yet. Format:

```json
{
  "transcript_id": "bcc-2026-05-19-special-and-regular-captions-text",
  "l1_sha256": "<sha256 of that L1 file>",
  "notice": "<the notice above, verbatim>",
  "producer": "<GitHub username, and tool and version if software was used>",
  "segments": [
    {
      "start": "04:46:05", "end": "04:46:19",
      "speaker": "off-blackaby",
      "attribution_level": "cued",
      "basis": "Chair: 'Moving on to Councilmember Bacabee' at 04:42:06"
    },
    {
      "start": "06:26:07", "end": "06:26:24",
      "speaker": "off-okeefe",
      "attribution_level": "verified",
      "basis": "Chair recognized O'Keefe at 06:24:52",
      "video_timestamp": "<video URL>#t=06:26:07",
      "verified_by": "<GitHub username>",
      "verified_on": "YYYY-MM-DD"
    }
  ]
}
```

Rules (full text in the root README): levels are exclusive; `verified` requires
`video_timestamp`, `verified_by` and `verified_on`; speakers come from
`registry/speakers.json` or are `public:<name as recorded>`; nothing is added from outside the
record; corrections cite a video timestamp or transcript cue.

## Transcript structure

`council-captions/<transcript>-structure.json`: one per caption transcript, dividing it into
agenda items and procedural blocks. Rules: root README, "L3 transcript structure". Each carries
this notice:

> Item boundaries in this layer are inferred, not authoritative. They were read from the
> transcript by software and volunteers, not by the City. The annotated agenda is the official
> record of what the Council took up and decided; verify a boundary against the meeting video
> before relying on it.

```json
{
  "transcript_id": "<id of the L1 caption text>",
  "l1_sha256": "<sha256 of that L1 file>",
  "notice": "<the notice above, verbatim>",
  "producer": "<GitHub username, and tool and version if software was used>",
  "segments": [
    {
      "start": "<HH:MM:SS>", "end": "<HH:MM:SS>",
      "meeting": "<YYYY-MM-DD> regular", "kind": "item", "item": "<as printed>",
      "public_comment": [{"start": "<HH:MM:SS>", "end": "<HH:MM:SS>"}],
      "evidence_start": "<words from the text where the item begins>",
      "level": "inferred"
    }
  ]
}
```

A transcript without timestamps uses `start_line` and `end_line` (line numbers in the L1 text)
in place of `start` and `end`.
