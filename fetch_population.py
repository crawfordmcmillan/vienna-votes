"""fetch_population.py — pull Census data for the Town of Vienna.

Place 51-81072 (the same GEOID as the town boundary on the precinct map).
Uses the Census Bureau's data.census.gov table API, which needs no key; one
raw JSON file per table per year is cached in data/population/.

Sources:
- Decennial census counts: 2000, 2010, 2020
- ACS 5-year estimates, 2010 through the latest release: population, median
  age, tenure, median home value, and median rent per year, plus the latest
  year's age, household, commute, year-built, education, and travel-time
  tables.
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

DATA = Path(__file__).parent / "data" / "population"
GEO = "160XX00US5181072"
BASE = "https://data.census.gov/api/access/data/table"
SLEEP_SECONDS = 0.2

DECENNIAL = {
    "dec_2000": "DECENNIALSF12000.P001",
    "dec_2010": "DECENNIALSF12010.P1",
    "dec_2020": "DECENNIALPL2020.P1",
}
SERIES_TABLES = ["B01003", "B01002", "B25003", "B25077", "B25064"]
DETAIL_TABLES = ["B01001", "B11001", "B08301", "B25034", "B25035",
                 "B15003", "B08303"]


def get_cached(session, name, table_id):
    out = DATA / f"{name}.json"
    if out.exists():
        print(f"cached  {name}.json")
        return True
    url = f"{BASE}?id={table_id}&g={GEO}"
    print(f"GET     {url}")
    resp = session.get(url, timeout=60)
    time.sleep(SLEEP_SECONDS)
    if resp.status_code != 200 or '"data"' not in resp.text:
        print(f"skip    {name}: HTTP {resp.status_code}")
        return False
    out.write_text(resp.text, encoding="utf-8")
    return True


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    session = requests.Session()

    for name, table_id in DECENNIAL.items():
        get_cached(session, name, table_id)

    latest = None
    for year in range(2010, 2026):
        if get_cached(session, f"acs_B01003_{year}", f"ACSDT5Y{year}.B01003"):
            latest = year
            for table in SERIES_TABLES[1:]:
                get_cached(session, f"acs_{table}_{year}", f"ACSDT5Y{year}.{table}")

    if latest:
        for table in DETAIL_TABLES:
            get_cached(session, f"acs_{table}_{latest}", f"ACSDT5Y{latest}.{table}")

    (DATA / "population_meta.json").write_text(
        json.dumps(
            {
                "source": "U.S. Census Bureau (data.census.gov table API): "
                          "decennial census 2000/2010/2020 and ACS 5-year "
                          "estimates, place 51-81072",
                "latest_acs": latest,
                "fetched_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"done    latest ACS year: {latest}")


if __name__ == "__main__":
    try:
        main()
    except requests.RequestException as e:
        print(f"error   {e}", file=sys.stderr)
        sys.exit(1)
