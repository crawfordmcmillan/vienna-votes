# Vienna VA Data

**Live site: https://viennavadata.org**

A static site showing how every Town of Vienna (VA) Council member voted on every
recorded item since the town's Legistar records begin in October 2013. Data comes
from the Town of Vienna's public Legistar records via the Granicus Legistar Web API.

## Publishing

The committed `site/` folder is deployed to GitHub Pages by
`.github/workflows/pages.yml` on every push to `main`. To update the site:

```
python fetch.py    # delete data/events.json first to pick up new meetings
python build.py
git add -A && git commit && git push
```

## Step 0 gate — findings (run 2026-07-25)

**Result: GO.** Vienna records roll-call votes as structured data.

- Client code: `vienna-va` (first guess worked)
- Town Council Meeting `BodyId`: **138** (16 bodies total; work sessions are body 180)
- 22 Town Council meetings between 2025-07-01 and 2026-07-25
- `GET /eventitems/{id}/votes` returns per-member records with `VotePersonName`
  and `VoteValueName` for substantive items (checked EventItems 37984, 37981, 37993
  from the 2026-05-18 meeting — 7 vote records each)
- Vote values observed: `"Aye"` and `"Nay"` — **not** "Yea". Preserved verbatim.
- Sanity check that it's real data: Roy Baldwin voted Nay on the FY2026-27 real
  estate tax rate intent (EventItem 37984) while the other six voted Aye.
- Useful raw fields confirmed present: `EventInSiteURL` (meeting link),
  `EventItemMatterId` + `EventItemMatterGuid` (item link via
  `LegislationDetail.aspx?ID={id}&GUID={guid}`), `EventItemConsent`,
  `EventItemActionText`, mover/seconder.

## Usage

```
python fetch.py   # pulls raw JSON from the Legistar API into data/ (cached; reruns are instant)
python build.py   # renders static HTML from data/ into site/ (no network)
```

Open `site/index.html` in a browser.

Dependencies: `pip install requests jinja2`

## Elections section

`fetch_elections.py` pulls precinct-level results for federal, statewide, and
Town of Vienna contests (2013–2025 November generals) from the Virginia
Department of Elections historical database, one raw CSV per contest in
`data/elections/`. The town is served by four Fairfax County precincts
(Vienna #1, #2, #4, #6 — confirmed as the complete set by the town's own 2023
council contest). Two caveats are stated on the page: precinct lines
approximate the town boundary for non-town races, and before ~2023 most
absentee ballots were counted in countywide central precincts that cannot be
attributed to precincts.

## Precinct map

`fetch_gis.py` caches two raw GeoJSON sources in `data/gis/`: Fairfax County's
voting precinct boundaries (county open data portal) and the Town of Vienna
corporate boundary (Census TIGERweb, place GEOID 5181072). `build.py` renders
them as a static inline SVG on the elections page — no JavaScript, no map
tiles, no external requests. The visible mismatch between the shaded precincts
and the red town line is the boundary approximation, shown rather than told.

## Topic categories (unofficial)

Vienna applies no topic tags in Legistar (38 index labels are defined in the
system but attached to zero matters), so `categorize.py` maintains this site's
own topical layer:

- Transparent title-pattern rules in `categorize.py`, plus exact-title
  assignments in `topic_assignments.json`, regenerate `categories.csv` in full
  on every run — the complete item-to-topic mapping is in that one reviewable
  file, and corrections belong in the rules or assignments, then regenerate.
- The site labels these categories "unofficial" everywhere they appear. They
  never alter or interpret the vote records themselves.

## Member profiles

Member pages show title, service history, and (for current members) official
town email — all taken verbatim from the Legistar `officerecords` and `persons`
endpoints. No authored biographical content.

## Notes

- `fetch.py` skips any call whose cache file already exists in `data/`. To refresh
  the meeting list (e.g. to pick up new meetings), delete `data/events.json` and
  rerun; per-item caches are keyed by ID and stay valid.
- Votes are fetched for **all** agenda items, not just those with a MatterId —
  procedural motions get roll calls too. Most items return `[]`; that's normal.
- `VoteValueName` is displayed exactly as returned. No scores, no rankings,
  no percentages, no interpretation.
