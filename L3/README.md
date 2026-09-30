# L3 — speaker attribution (not authoritative)

> Speaker attribution in this layer is inferred, not authoritative. It was produced by software
> and volunteers, not by the City. Verify any attribution against the meeting video before
> relying on it.

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
