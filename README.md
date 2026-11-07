# STM TripModifications, repaired and rebuilt

This branch holds the current output of
[stm-tripmodifications-fix](https://github.com/TransitApp/stm-tripmodifications-fix)
and nothing else. It is replaced from scratch on every run, so it has no history.

Two feeds sit here, built from two different sources.

## The STM's feed, repaired

| File | What it is |
| --- | --- |
| `tripmodifications.pb` | The STM's GTFS-RT feed with its cancelled ranges repaired |
| `tripmodifications.json` | The same feed as JSON |
| `report.md` | What was repaired, for a person |
| `stm-tripmodifications-report.pdf` | A before/after map of every repair |
| `report.json` | The same as `report.md`, for a program |
| `metadata.json` | When the run happened and how many entities it touched |

## Built from the STM website, under `web/`

| File | What it is |
| --- | --- |
| `web/tripmodifications.pb` | Modifications built from the detours stm.info publishes |
| `web/tripmodifications.json` | The same feed as JSON |
| `web/report.md` | Every detour it found, for a person |
| `web/stm-detours-report.pdf` | A map of every detour in that feed |
| `web/report.json` | The same, for a program |
| `web/metadata.json` | When the run happened and what it read |

The website says outright which stops a detour skips and which it serves
instead, so this feed does not have to guess at either. It covers detours the
STM's own feed carries with no modifications at all, and route variants the
STM's feed leaves out. What it cannot do is name a temporary stop the website
does not list; `web/report.md` says when that happened.

The website feed writes today's service date and the six after it: the website
says nothing about how long a detour lasts, so each date carries the detours as
they stand now.

The repaired feed and its reports are rebuilt every run; the website feed once
an hour, because reading it costs about 570 requests to stm.info. Each PDF is
drawn only when what it draws changes, when asked for, or when this branch has
none, so a PDF can be older than the rest — its cover page carries the feed
timestamp it was drawn from.

Source data: Société de transport de Montréal (STM), licensed
[CC-BY 4.0](https://www.stm.info/en/about/developers/terms-use).
This is an unofficial derived feed, not published or endorsed by the STM.
