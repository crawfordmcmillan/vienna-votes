"""fetch_crashes.py — pull crash records for the Town of Vienna.

Source: VDOT's statewide crash data layer (published on the Virginia Roads
open data portal), filtered to crashes VDOT itself attributes to the Town of
Vienna (PHYSICAL_JURIS = '153. Town of Vienna'). Raw JSON pages cached in
data/crashes/. Reportable crashes only, per state rules: a fatality, an
injury, or at least $1,500 in damage. The layer begins in 2017.

Delete data/crashes/ to refetch; the weekly refresh does this to pick up
new records and VDOT's revisions.
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

DATA = Path(__file__).parent / "data" / "crashes"
SLEEP_SECONDS = 0.1
URL = ("https://services.arcgis.com/p5v98VHDX9Atv3l7/arcgis/rest/services/"
       "CrashData_test/FeatureServer/2/query")
PAGE = 2000


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    offset = 0
    while True:
        out = DATA / f"crashes_{offset}.json"
        if out.exists():
            print(f"cached  {out.name}")
            data = json.loads(out.read_text(encoding="utf-8"))
        else:
            print(f"GET     {URL} offset={offset}")
            resp = session.get(URL, params={
                "where": "PHYSICAL_JURIS = '153. Town of Vienna'",
                "outFields": "*",
                "returnGeometry": "true",
                "outSR": 4326,
                "orderByFields": "OBJECTID",
                "resultOffset": offset,
                "resultRecordCount": PAGE,
                "f": "json",
            }, timeout=120)
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                raise RuntimeError(data["error"])
            out.write_text(resp.text, encoding="utf-8")
            time.sleep(SLEEP_SECONDS)
        if len(data.get("features", [])) < PAGE:
            break
        offset += PAGE

    (DATA / "crashes_meta.json").write_text(
        json.dumps(
            {
                "source": "VDOT statewide crash data (Virginia Roads open data), "
                          "filtered to PHYSICAL_JURIS = '153. Town of Vienna'",
                "fetched_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print("done")


if __name__ == "__main__":
    try:
        main()
    except (requests.RequestException, RuntimeError) as e:
        print(f"error   {e}", file=sys.stderr)
        sys.exit(1)
