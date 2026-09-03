# stm-tripmodifications-fix

The [STM](https://www.stm.info)'s GTFS-RT
[TripModifications](https://gtfs.org/documentation/realtime/feed-entities/trip-modifications/)
feed describes detours correctly in the middle of a line and wrongly at the
ends of one. This repairs the ends and republishes the feed.

It also builds a **second feed from a different source**: the detours the STM
publishes on its own website, which say outright which stops a detour skips and
which it serves instead, so nothing has to be inferred from a shape. See
[Building it from the website](#building-it-from-the-website).

The repair runs every ten minutes on GitHub Actions and the website build once
an hour, both from the same workflow. The current output lives on the
[`output`](../../tree/output) branch, which is replaced whole on every run and
so keeps no history:

| File | What it is |
| --- | --- |
| [`tripmodifications.pb`](https://raw.githubusercontent.com/TransitApp/stm-tripmodifications-fix/output/tripmodifications.pb) | the repaired GTFS-RT feed |
| [`tripmodifications.json`](https://raw.githubusercontent.com/TransitApp/stm-tripmodifications-fix/output/tripmodifications.json) | the same feed as JSON |
| [`report.md`](https://raw.githubusercontent.com/TransitApp/stm-tripmodifications-fix/output/report.md) | what was repaired, written out |
| [`stm-tripmodifications-report.pdf`](https://raw.githubusercontent.com/TransitApp/stm-tripmodifications-fix/output/stm-tripmodifications-report.pdf) | a before/after map of every repair |
| [`web/tripmodifications.pb`](https://raw.githubusercontent.com/TransitApp/stm-tripmodifications-fix/output/web/tripmodifications.pb) | the feed built from the website |
| [`web/report.md`](https://raw.githubusercontent.com/TransitApp/stm-tripmodifications-fix/output/web/report.md) | every detour it found, written out |

`report.json` and `metadata.json` sit beside each of them for anything reading
this by machine, as does `web/tripmodifications.json`.

Everything but the PDF is rebuilt every run. The PDF is redrawn only when the
repairs change: drawing it downloads map tiles, and detours turn over on the order of
hours, so a run whose repairs match the published ones carries the existing PDF
forward instead. It is also redrawn when [asked](#refreshing-the-pdf) and when
the branch has none, so a missing one comes back by itself.

The comparison ignores what moves between runs without changing the maps — the
feed timestamp, which trip was sampled, and the measured distances. Everything
the pages show is in it: which entities were repaired, how each range moved,
and which stops were added or dropped.

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

## Running the repair

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

## Building it from the website

The repair above can only narrow what the STM's feed already says. Where the
feed says nothing — 19 of its 168 entities carried a detour shape and no
modification at all in one snapshot — there is nothing to narrow. So a second
tool ignores the feed and builds the modifications from the source the STM
already publishes for its own line pages.

Three endpoints, per line and direction:

```
/pub/i3/v1c/api/fr/lines
/pub/i3/v1c/api/fr/lines/51/stops?direction=W&withconnection=0&detoured=1&canceled=1
/pub/i3/v1c/api/fr/lines/51/routes/default?direction=W&detoured=1&canceled=1
```

They need no key. They do refuse any request without an `Origin` header naming
`https://www.stm.info`.

**There is no endpoint listing which lines are detoured** — `lines/detours` and
the like are just `lines/{id}` matching a line called "detours" — so finding out
means asking for all of them. One run is **1 request for the line list, 407 for
the stop lists, one per line and direction, and one more for each of the ~165
that turn out to be detoured: about 570**, a megabyte or so gzipped.

That is the whole cost, and two things keep it modest. The requests are paced
at **20 a second** rather than sent as fast as four threads can manage, which is
several hundred a second on replies this small. And the workflow reads the
website **once an hour**, not on every ten-minute run, measured against the
published feed's own timestamp so a dropped run does not skip a turn. Detours
turn over on the order of hours, and the GTFS-RT spec's own service-level
objective for TripModifications is about hourly.

### Why not read the realtime feed for the list

The STM's realtime feed already names the detoured lines in its entity IDs, and
asking the website only about those would halve the requests. It would also lose
a fifth of the detours. Measured on two feeds sampled minutes apart:

| | Route directions |
| --- | --- |
| Named by the realtime feed | 147 |
| Flagged as detoured by the website | 163 |
| In both | 129 |
| **Website only** | **34** |
| Realtime feed only | 18 |

The 34 are not edge cases. 368 East skips seventeen stops and serves thirteen
others, 100 West skips six and serves three, 460 West skips five and serves two.
None of them has an entity in the realtime feed at all, not even an empty one.
They are exactly what this feed exists to carry.

The 18 the other way are the opposite case: fourteen carry no modification at
all — a shape that moved while the stops did not — and the rest describe a
detour the website has stopped showing.

Reading the feed for the list would also make this need the STM API credentials
the repair uses, and tie the second source to the feed it exists to check.

The stop list is what makes this worth doing. With `detoured=1&canceled=1` each
stop carries two flags: **`cxl`** on a stop the detour skips, and **`dtr`** on a
stop it serves instead. The cancelled range and the replacement stops are read
straight off them. Nothing is measured against a shape to decide either, which
is what the repair has to do and what it can only get approximately right.

The route endpoint supplies `Geometry`, the scheduled shape, plus `canceled` and
`detoured`: the runs of road the detour leaves and the runs it takes instead,
each pair sharing its two end points exactly. Geometry is still needed for three
things, and only these three:

1. **Ordering the replacement stops.** The website appends them to the end of
   its list rather than in service order, so each is projected onto the detour
   it stands on and sorted by how far along it falls.
2. **Matching each run of skipped stops to the detour that replaces it**, when a
   line has more than one, by where they fall on the trip's shape.
3. **The new shape**, made by splicing each detour into the trip's own
   `shapes.txt` geometry where it leaves the line.

The flags are read against the line's scheduled stop list, so they apply to
**every route pattern of that line and direction** whose trips run today, not
only the one the website draws. A short turn over the same closed street gets
the same modification, with the stop sequences its own pattern uses.

### What it produces

Measured on one snapshot: 407 line directions read, 164 of them detoured, 194
`TripModifications` entities, 252 modifications and 86 temporary stops. The
STM's own feed at the same moment had 168 entities and 183 modifications.

Matching the two by the trips they name: 145 entities in both, 125 with the
same spans, 110 identical outright. 49 entities have no counterpart in the STM's
feed at all — mostly route variants it leaves out.

The two disagree most often at a terminus, and the same way every time: the STM
cancels the end stop and then appends it to its own replacement list, so the
trip still calls there. This feed writes the shorter thing — the stop is not
cancelled — which is the same service either way.

### What it cannot do

- **It cannot name a temporary stop the website does not list.** Five
  replacement stops were left out that way in that snapshot, and four of the
  STM's 168 entities describe a detour the website does not. `web/report.md`
  names both.
- **It writes today's service date and no other.** A night trip that runs past
  midnight belongs to the previous service date and is left out until that date
  comes round, so between midnight and the small hours the night lines are
  short. The website carries no dates at all, so anything further ahead would be
  a guess.
- **A modification that adds stops without dropping any** needs a
  `start_stop_selector` all the same. The stop the detour leaves from is named
  as the span and put back into the replacement list where the detour passes it,
  which leaves the trip calling at it exactly as before.

### Running the website build

```bash
pip install -e ".[dev]"
python -m tmweb --verbose
```

It needs no credentials — only the static GTFS, which it shares with the repair
above and caches the same way. Output lands in `./output-web`.

| Option | What it does |
| --- | --- |
| `--service-date 20260903` | write this date instead of today in Montreal |
| `--rate 20` | most requests to send a second |
| `--workers 4` | how many requests to have in flight at once |
| `--output-dir DIR` | where to write the artifacts |
| `--cache-dir DIR` | where to keep the static feed and its parsed form |

Reading all 407 line directions takes about thirty seconds at the default rate.

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

The credentials are the repair's, not the website build's. Without them the
repair step fails and the run stops before publishing anything.

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
- **The website build is a guest on stm.info.** One run makes about 570
  requests, paced at 20 a second, and the workflow only lets it run once an
  hour — `WEB_MAX_AGE_MINUTES` in the workflow sets that. None of it is covered
  by the developer terms the repair runs under, so leave it slow.
- **`raw.githubusercontent.com` is rate limited.** Unauthenticated requests
  fall under GitHub's 60-per-hour limit, and responses are cached for about
  five minutes. This is fine for a few consumers looking at the data. It is not
  a production feed endpoint, and running one on GitHub Actions would be the
  kind of use its terms rule out.

## Data and licence

Source data: Société de transport de Montréal (STM), licensed
[CC-BY 4.0](https://www.stm.info/en/about/developers/terms-use) — the developer
feeds for the repair, the website's own line-page API for the second feed. Both
are unofficial derived feeds, neither published nor endorsed by the STM.
Attribution will be removed at the STM's request.

The code is MIT licensed. See [LICENSE](LICENSE).
