"""fetch_gis.py — pull boundary geometry for the precinct map.

Two raw GeoJSON sources, cached verbatim in data/gis/:
- Fairfax County voting precincts (county open data portal, all ~266
  precincts; the build filters to the four Vienna precincts by PREC_IDENT)
- Town of Vienna corporate boundary (Census TIGERweb, incorporated place
  GEOID 5181072)
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

DATA = Path(__file__).parent / "data" / "gis"

SOURCES = {
    "fairfax_precincts.geojson": (
        "https://data-fairfaxcountygis.opendata.arcgis.com/api/download/v1/"
        "items/7727de78b3984f4daa1ff0960d0da8cb/geojson?layers=1"
    ),
    "vienna_boundary.geojson": (
        "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/"
        "Places_CouSub_ConCity_SubMCD/MapServer/4/query"
        "?where=GEOID%3D%275181072%27&outFields=GEOID,NAME&outSR=4326&f=geojson"
    ),
}


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    for name, url in SOURCES.items():
        out = DATA / name
        if out.exists():
            print(f"cached  {name}")
            continue
        print(f"GET     {url}")
        resp = requests.get(url, timeout=120)
        resp.raise_for_status()
        # Strip a UTF-8 BOM if present so downstream json.load is simple.
        out.write_bytes(resp.content.lstrip(b"\xef\xbb\xbf"))
    (DATA / "gis_meta.json").write_text(
        json.dumps(
            {
                "sources": SOURCES,
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
    except requests.RequestException as e:
        print(f"error   {e}", file=sys.stderr)
        sys.exit(1)
