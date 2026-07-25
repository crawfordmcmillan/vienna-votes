# Vienna Votes

A static site showing how every Town of Vienna (VA) Council member voted on every
recorded item in the last 12 months. Data comes from the Town of Vienna's public
Legistar records via the Granicus Legistar Web API.

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

## Notes

- `fetch.py` skips any call whose cache file already exists in `data/`. To refresh
  the meeting list (e.g. to pick up new meetings), delete `data/events.json` and
  rerun; per-item caches are keyed by ID and stay valid.
- Votes are fetched for **all** agenda items, not just those with a MatterId —
  procedural motions get roll calls too. Most items return `[]`; that's normal.
- `VoteValueName` is displayed exactly as returned. No scores, no rankings,
  no percentages, no interpretation.
