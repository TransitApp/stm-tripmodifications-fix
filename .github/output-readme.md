# Repaired STM TripModifications

This branch holds the current output of
[stm-tripmodifications-fix](https://github.com/TransitApp/stm-tripmodifications-fix)
and nothing else. It is replaced from scratch on every run, so it has no history.

| File | What it is |
| --- | --- |
| `tripmodifications.pb` | The repaired GTFS-RT feed |
| `tripmodifications.json` | The same feed as JSON |
| `report.md` | What was repaired, for a person |
| `stm-tripmodifications-report.pdf` | A before/after map of every repair |
| `report.json` | The same as `report.md`, for a program |
| `metadata.json` | When the run happened and how many entities it touched |

The feed and the reports are rebuilt every run. The PDF is drawn only when
asked for, or when this branch has none, so it can be older than the rest — its
cover page carries the feed timestamp it was drawn from.

Source data: Société de transport de Montréal (STM), licensed
[CC-BY 4.0](https://www.stm.info/en/about/developers/terms-use).
This is an unofficial derived feed, not published or endorsed by the STM.
