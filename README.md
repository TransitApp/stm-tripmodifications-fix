# stm-tripmodifications-fix

The [STM](https://www.stm.info)'s GTFS-RT
[TripModifications](https://gtfs.org/documentation/realtime/feed-entities/trip-modifications/)
feed describes detours correctly in the middle of a line and wrongly at the
ends of one. This repairs the ends and republishes the feed.

It runs every ten minutes on GitHub Actions. The current output lives on the
[`output`](../../tree/output) branch, which is replaced whole on every run and
so keeps no history:

| File | What it is |
| --- | --- |
| [`tripmodifications.pb`](https://raw.githubusercontent.com/TransitApp/stm-tripmodifications-fix/output/tripmodifications.pb) | the repaired GTFS-RT feed |
| [`tripmodifications.json`](https://raw.githubusercontent.com/TransitApp/stm-tripmodifications-fix/output/tripmodifications.json) | the same feed as JSON |
| [`report.md`](https://raw.githubusercontent.com/TransitApp/stm-tripmodifications-fix/output/report.md) | what was repaired, written out |
| [`stm-tripmodifications-report.pdf`](https://raw.githubusercontent.com/TransitApp/stm-tripmodifications-fix/output/stm-tripmodifications-report.pdf) | a before/after map of every repair |

`report.json` and `metadata.json` sit beside them for anything reading this by
machine.

The first three are rebuilt every run. The PDF is not: drawing it downloads map
tiles, so a scheduled run carries the existing one forward and only draws a new
one when [asked](#refreshing-the-pdf) or when the branch has none.

## The bug

A modification says which run of stops a detour skips, using
`start_stop_selector` and `end_stop_selector`, and lists the stops served
instead in `replacement_stops`. In the middle of a line the STM gets this
right — of 158 mid-line modifications measured on one snapshot, 157 were
correct.

At a terminus it collapses the range to a single stop and appends the stop it
just cancelled to the replacement list, however many stops the detour really
skips. Every one of the 14 start-of-line modifications in that snapshot
declared `[1..1]`, and 13 of them ended their replacement list with the stop at
sequence 1.

Route 97 East is the clearest case. Its detour leaves du Mont-Royal at the
terminus, runs sixteen stops east along Saint-Joseph, and rejoins at
Parthenais. What the feed says:

```
start_stop_selector { stop_sequence: 1 }
end_stop_selector   { stop_sequence: 1 }
replacement_stops   [ temp_stop_61663, ...twelve Saint-Joseph stops..., 54010 ]
```

Stops 2 to 16 stay in the trip although the bus is four hundred metres away on
another street, and `54010` — the terminus the bus has just left — is added
back as a replacement. What this tool writes instead:

```
start_stop_selector { stop_sequence: 1 }
end_stop_selector   { stop_sequence: 16 }
replacement_stops   [ temp_stop_61663, ...twelve Saint-Joseph stops... ]
```

## How it repairs

Each modification carries its own detour shape, and that shape settles what the
vehicle actually does. Two rules follow:

1. **Extend the cancelled range** through the run of stops next to the declared
   range that lie farther than the threshold from the detour shape. It stops at
   the edge of any range another modification already claims, since the spec
   forbids modifications with overlapping spans.
2. **Drop replacement stops** that lie farther than the threshold from the
   detour shape.

The threshold defaults to **100 m**. It is not a delicate number: stops the
vehicle does serve measure 0 to 26 m from the shape, and the errors measure 94
to 468 m. Anything from about 80 m to 150 m picks out the same modifications.

### What it will not do

The repair narrows what a modification claims. It never removes one. Every
entity and every modification in the input appears in the output, because a
detour we do not understand is still better than no detour, and `report.md`
lists everything passed through and why. It stops short when:

- the entity has no usable detour shape, or no selected trip in the static feed;
- the selected trips do not share one stop pattern, so a `stop_sequence` would
  mean a different stop for each;
- the trip would be left with fewer than two stops;
- the replacement stops carry `travel_time_to_stop`, which is counted from the
  stop before the range. The range may then only grow forwards, since moving
  its start would silently change what those times mean. The STM sets no travel
  times today, so this does not come up for them.

A stop with no known position is never treated as skipped: not knowing where a
stop is says nothing about whether the bus reaches it.

## Running it

```bash
pip install -e ".[dev]"

export STM_API_USERNAME=...   # from https://developpeurs.stm.info
export STM_API_PASSWORD=...
python -m tmfix --verbose
```

Output lands in `./output`. Useful options:

| Option | What it does |
| --- | --- |
| `--threshold 150` | metres from the shape beyond which a stop counts as unserved |
| `--realtime-file feed.pb` | read a saved feed instead of fetching one |
| `--output-dir DIR` | where to write the artifacts |
| `--cache-dir DIR` | where to keep the static feed and its parsed form |

The static GTFS is fetched conditionally and only re-parsed when the STM
publishes a new one, so a normal run downloads about 250 KB.

Tests and linting:

```bash
pytest -q
ruff check . && ruff format --check .
```

## The map report

Every repair is also drawn as a page in a PDF: the same detour shape in two
panels, the stops as the feed claims them on the left and as the shape implies
them on the right, over a basemap. It is what to send an agency, because it
shows the vehicle leaving the stops behind rather than only asserting it.

```bash
pip install -e ".[report]"
python -m tmreport --output report.pdf
```

| Option | What it does |
| --- | --- |
| `--realtime-file feed.pb` | draw a saved feed instead of fetching one |
| `--no-basemap` | skip the street tiles, which is faster and needs no network |
| `--tile-cache-dir DIR` | where downloaded tiles are kept |

Notes on the drawing:

- The basemap is Esri's grey canvas. CartoDB Positron now returns watermarked
  "API KEY REQUIRED" tiles; Esri's needs no key and reads the same way. Its
  tiles stop at zoom 16, so the code clamps to that.
- A long cancelled run gets only its end stops named. Every stop still gets a
  marker, and the page header gives the count.

### Refreshing the PDF

On the **Actions** tab, pick **Repair and publish**, choose **Run workflow**,
and tick **Rebuild the PDF map report**. The run redraws it from the feed it
has just fetched and publishes it with the rest.

## Running it yourself

Fork it, then set `STM_API_USERNAME` and `STM_API_PASSWORD` as repository
secrets under **Settings → Secrets and variables → Actions**. Scheduled
workflows are disabled on new forks; enable them on the **Actions** tab.

### Operational caveats

- **The schedule is approximate.** GitHub's minimum is five minutes, and
  scheduled runs "can be delayed during periods of high load" and are
  occasionally dropped. The cron is offset from the top of the hour, which is
  when delays are worst. Do not count on exact ten-minute spacing.
- **The schedule switches itself off.** On a public repository, scheduled
  workflows are disabled automatically after 60 days with no repository
  activity. GitHub emails first. To turn it back on, open the **Actions** tab,
  pick **Repair and publish**, and choose **Enable workflow** — or call
  `PUT /repos/{owner}/{repo}/actions/workflows/{id}/enable`. The publish step
  pushes a commit on every run, which may be enough to count as activity, but
  GitHub does not promise that.
- **`raw.githubusercontent.com` is rate limited.** Unauthenticated requests
  fall under GitHub's 60-per-hour limit, and responses are cached for about
  five minutes. This is fine for a few consumers looking at the data. It is not
  a production feed endpoint, and running one on GitHub Actions would be the
  kind of use its terms rule out.

## Data and licence

Source data: Société de transport de Montréal (STM), licensed
[CC-BY 4.0](https://www.stm.info/en/about/developers/terms-use). This is an
unofficial derived feed, neither published nor endorsed by the STM.
Attribution will be removed at the STM's request.

The code is MIT licensed. See [LICENSE](LICENSE).
