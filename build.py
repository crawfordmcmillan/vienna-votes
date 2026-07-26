"""build.py — render static HTML from data/ into site/. No network calls.

Reads the raw JSON cached by fetch.py and writes five page types:
index, one page per council member, one per meeting, one per agenda item,
and one per topic (if categories.csv exists). VoteValueName is carried
verbatim everywhere; topic categories are this site's own unofficial layer,
kept in categories.csv and labeled as such.
"""
import csv
import json
import math
import re
import shutil
from collections import Counter, defaultdict
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).parent
DATA = ROOT / "data"
SITE = ROOT / "site"
TEMPLATES = ROOT / "templates"
CATEGORIES = ROOT / "categories.csv"
ALIASES = ROOT / "person_aliases.json"
LEGISTAR = "https://vienna-va.legistar.com"
REPO = "https://github.com/crawfordmcmillan/viennavadata"
BASE_URL = "https://viennavadata.org"
CNAME_DOMAIN = "viennavadata.org"


def load(name):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")


def pdf_url(url: str | None) -> str | None:
    # Older cached records carry http:// Granicus links; the host serves https.
    return url.replace("http://", "https://", 1) if url else None


def last_name(name: str) -> str:
    suffixes = {"jr", "sr", "ii", "iii", "iv"}
    parts = [p for p in name.replace(",", "").split() if p.rstrip(".").lower() not in suffixes]
    return parts[-1] if parts else name


def fmt_date(iso: str | None) -> str:
    return iso[:10] if iso else ""


ELECTIONS = ROOT / "data" / "elections"
OFFICE_ORDER = ["President", "U.S. Senate", "U.S. House", "Governor",
                "Lieutenant Governor", "Attorney General",
                "Mayor, Town of Vienna", "Town Council, Town of Vienna"]


def parse_contest(contest):
    """Parse one raw contest CSV into Vienna-precinct rows, verbatim values."""
    rows = list(csv.reader((ELECTIONS / f"contest_{contest['id']}.csv").open(encoding="utf-8-sig")))
    header, parties = rows[0], rows[1]
    cols, candidates = [], []
    for i in range(2, len(header)):
        name = header[i].strip()
        if not name or name.startswith("Total") or name in ("Undervotes", "Overvotes"):
            continue
        cols.append(i)
        party = parties[i].strip() if i < len(parties) else ""
        candidates.append({"name": name, "party": party})
    stripped = [h.strip() for h in header]
    total_idx = (stripped.index("Total Ballots Cast")
                 if "Total Ballots Cast" in stripped
                 else stripped.index("Total Votes Cast"))

    def num(r, i):
        s = (r[i] if i < len(r) else "").replace(",", "").strip()
        return int(s) if s else 0

    is_town = contest.get("town", False)
    in_scope = False
    precincts = []
    central_absentee = 0
    for r in rows[2:]:
        if len(r) < 2:
            continue
        kind, name = r[0], r[1]
        if kind in ("Locality", "Town", "Congressional District"):
            in_scope = ("Fairfax County" in name) or (kind == "Town" and name == "Vienna")
        elif kind == "Precinct" and in_scope:
            if "Vienna #" in name or (is_town and name == "Provisional"):
                precincts.append({
                    "name": name,
                    "votes": [num(r, i) for i in cols],
                    "total": num(r, total_idx),
                })
            elif "Absentee" in name:
                central_absentee += num(r, total_idx)
    totals = [sum(p["votes"][j] for p in precincts) for j in range(len(cols))]
    return {
        **contest,
        "candidates": candidates,
        "precincts": precincts,
        "totals": totals,
        "ballots": sum(p["total"] for p in precincts),
        "total_label": "ballots" if "Total Ballots Cast" in stripped else "total votes",
        "central_absentee": central_absentee,
        "source_url": f"https://historical.elections.virginia.gov/contest/{contest['id']}",
    }


GIS = ROOT / "data" / "gis"
MAP_PRECINCTS = {213: "Vienna #1", 214: "Vienna #2", 216: "Vienna #4", 218: "Vienna #6"}
MAP_FILLS = {213: "#ead9b7", 214: "#cfd8c2", 216: "#e5c6b2", 218: "#c9d3da"}
# Local roads worth drawing for orientation (TIGER BASENAME values); secondary
# roads (Rt 123/Maple, Nutley, Chain Bridge, Leesburg Pike) are all drawn.
MAP_LOCAL_ROADS = {"Church", "Beulah", "Park", "Courthouse", "Lawyers",
                   "Cedar", "Old Courthouse", "Follin", "Glyndon"}
MAP_ROAD_LABELS = ["Maple Ave", "Nutley St", "Church St", "Beulah Rd",
                   "Lawyers Rd", "Courthouse Rd", "Chain Bridge Rd"]


def build_precinct_map():
    """Render the four Vienna precincts + town boundary as a static inline SVG."""
    precincts_file = GIS / "fairfax_precincts.geojson"
    boundary_file = GIS / "vienna_boundary.geojson"
    if not (precincts_file.exists() and boundary_file.exists()):
        return None

    precincts = {}
    polling = []
    for f in json.loads(precincts_file.read_text(encoding="utf-8"))["features"]:
        ident = f["properties"].get("PREC_IDENT")
        if ident in MAP_PRECINCTS:
            geom = f["geometry"]
            rings = geom["coordinates"] if geom["type"] == "Polygon" else \
                [r for poly in geom["coordinates"] for r in poly]
            precincts[ident] = rings
            p = f["properties"]
            polling.append({
                "name": MAP_PRECINCTS[ident],
                "place": p.get("POLLING_PLACE"),
                "address": f"{p.get('ADDRESS')}, {p.get('CITY')}, VA {p.get('ZIP')}",
            })
    polling.sort(key=lambda p: p["name"])
    boundary = json.loads(boundary_file.read_text(encoding="utf-8"))["features"][0]["geometry"]["coordinates"]

    all_pts = [pt for rings in list(precincts.values()) + [boundary] for ring in rings for pt in ring]
    lons = [p[0] for p in all_pts]
    lats = [p[1] for p in all_pts]
    mid_lat = (min(lats) + max(lats)) / 2
    kx = math.cos(math.radians(mid_lat))
    span_x = (max(lons) - min(lons)) * kx
    span_y = max(lats) - min(lats)
    width, pad = 760.0, 16.0
    scale = (width - 2 * pad) / span_x
    height = span_y * scale + 2 * pad

    def xy(pt):
        x = (pt[0] - min(lons)) * kx * scale + pad
        y = (max(lats) - pt[1]) * scale + pad
        return x, y

    def path(rings):
        parts = []
        for ring in rings:
            parts.append("M" + "L".join(f"{x:.1f} {y:.1f}" for x, y in (xy(p) for p in ring)) + "Z")
        return "".join(parts)

    def centroid(ring):
        a = cx = cy = 0.0
        pts = [xy(p) for p in ring]
        for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
            cross = x1 * y2 - x2 * y1
            a += cross
            cx += (x1 + x2) * cross
            cy += (y1 + y2) * cross
        a *= 3
        return (cx / a, cy / a) if a else pts[0]

    def road_lines(feature):
        geom = feature["geometry"]
        return geom["coordinates"] if geom["type"] == "MultiLineString" else [geom["coordinates"]]

    def line_path(lines):
        return "".join(
            "M" + "L".join(f"{x:.1f} {y:.1f}" for x, y in (xy(p) for p in line))
            for line in lines
        )

    secondary = json.loads((GIS / "roads_secondary.geojson").read_text(encoding="utf-8"))["features"]
    local = [
        f for f in json.loads((GIS / "roads_local.geojson").read_text(encoding="utf-8"))["features"]
        if f["properties"].get("BASENAME") in MAP_LOCAL_ROADS
    ]

    svg = [f'<svg viewBox="0 0 {width:.0f} {height:.0f}" role="img" '
           f'aria-label="Map of the four Vienna voting precincts, the town boundary, '
           f'major roads, and polling places" xmlns="http://www.w3.org/2000/svg">']
    for ident, rings in sorted(precincts.items()):
        svg.append(f'<path d="{path(rings)}" fill="{MAP_FILLS[ident]}" '
                   f'stroke="#16150f" stroke-width="1.2" stroke-linejoin="round"/>')
    for f in local:
        svg.append(f'<path d="{line_path(road_lines(f))}" fill="none" stroke="#aca38f" '
                   f'stroke-width="1.4" stroke-linecap="round"/>')
    for f in secondary:
        svg.append(f'<path d="{line_path(road_lines(f))}" fill="none" stroke="#9b917c" '
                   f'stroke-width="2.4" stroke-linecap="round"/>')
    svg.append(f'<path d="{path(boundary)}" fill="none" stroke="#d1461f" '
               f'stroke-width="3" stroke-linejoin="round"/>')
    for ident, rings in sorted(precincts.items()):
        cx, cy = centroid(max(rings, key=len))
        svg.append(f'<text x="{cx:.0f}" y="{cy:.0f}" text-anchor="middle" '
                   f'font-family="Archivo, Arial, sans-serif" font-weight="900" '
                   f'font-size="26" fill="#16150f">#{MAP_PRECINCTS[ident][-1]}</text>')

    # Road labels: midpoint of the longest drawn segment for each labeled name.
    all_roads = secondary + local
    for label in MAP_ROAD_LABELS:
        candidates = [f for f in all_roads if (f["properties"].get("NAME") or "").startswith(label)]
        if not candidates:
            continue
        lines = road_lines(max(candidates, key=lambda f: sum(len(l) for l in road_lines(f))))
        line = max(lines, key=len)
        mx, my = xy(line[len(line) // 2])
        mx = min(max(mx, 60), width - 60)
        my = min(max(my, 20), height - 10)
        svg.append(f'<text x="{mx:.0f}" y="{my - 4:.0f}" text-anchor="middle" '
                   f'font-family="Archivo, Arial, sans-serif" font-weight="600" font-size="12" '
                   f'fill="#6d675c" stroke="#f7f3ec" stroke-width="3" '
                   f'paint-order="stroke" letter-spacing="0.04em">{label}</text>')

    # Polling place markers.
    pp_file = GIS / "fairfax_polling_places.geojson"
    if pp_file.exists():
        for f in json.loads(pp_file.read_text(encoding="utf-8"))["features"]:
            p = f["properties"]
            idents = {p.get("PREC_IDENT"), p.get("PREC_IDENT2")}
            if idents & set(MAP_PRECINCTS):
                x, y = xy(f["geometry"]["coordinates"])
                name = (p.get("DESCRIPTION") or "").title() \
                    .replace("Elementary School", "ES").replace("High School", "HS")
                # End-anchored labels drop below the dot so they don't collide
                # with the big precinct number sitting to their left.
                if x > width * 0.7:
                    anchor, tx, ty = "end", x - 2, y + 20
                else:
                    anchor, tx, ty = "start", x + 9, y + 4
                svg.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5.5" fill="#16150f" '
                           f'stroke="#f7f3ec" stroke-width="2"/>')
                svg.append(f'<text x="{tx:.0f}" y="{ty:.0f}" text-anchor="{anchor}" '
                           f'font-family="Archivo, Arial, sans-serif" font-weight="700" '
                           f'font-size="12" fill="#16150f" stroke="#f7f3ec" stroke-width="3" '
                           f'paint-order="stroke">{name}</text>')

    svg.append("</svg>")
    return {"svg": "".join(svg), "polling": polling}


def load_elections():
    meta_file = ELECTIONS / "elections_meta.json"
    if not meta_file.exists():
        return None, []
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    contests = [parse_contest(c) for c in meta["contests"]]
    by_date = defaultdict(list)
    for c in contests:
        by_date[c["date"]].append(c)
    elections = []
    for date in sorted(by_date, reverse=True):
        group = sorted(by_date[date], key=lambda c: OFFICE_ORDER.index(c["office"]))
        elections.append({"date": date, "contests": group})
    return meta, elections


def main():
    meta = load("fetch_meta.json")
    today = meta["fetched_at_utc"][:10]
    events = sorted(load("events.json"), key=lambda e: e["EventDate"], reverse=True)

    # Optional unofficial topic layer: categories.csv (id,date,title,category).
    category_of = {}
    if CATEGORIES.exists():
        with CATEGORIES.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                category_of[int(row["id"])] = row["category"]

    # Assemble meetings -> items -> votes, all verbatim from the cache.
    meetings = []
    items_by_id = {}
    for event in events:
        items = sorted(
            load(f"eventitems_{event['EventId']}.json"),
            key=lambda i: (i.get("EventItemMinutesSequence") or i.get("EventItemAgendaSequence") or 0),
        )
        date = event["EventDate"][:10]
        meeting = {
            "event": event,
            "date": date,
            "slug": date,
            "items": [],
            "minutes_pdf": pdf_url(event.get("EventMinutesFile")),
            "agenda_pdf": pdf_url(event.get("EventAgendaFile")),
        }
        for item in items:
            votes = load(f"votes_{item['EventItemId']}.json")
            tally = Counter(v["VoteValueName"] for v in votes)
            entry = {
                "item": item,
                "votes": votes,
                "tally": ", ".join(
                    f"{n} {value if value is not None else '(no value recorded)'}"
                    for value, n in tally.items()
                ),
                # Vienna's InSite site keys legislation pages by internal web IDs
                # the public API doesn't expose, so per-matter deep links can't be
                # built; the meeting record page (from the API verbatim) shows the
                # item with its roll call and always resolves.
                "source_url": event["EventInSiteURL"],
                "meeting": meeting,
                "category": category_of.get(item["EventItemId"]),
            }
            meeting["items"].append(entry)
            items_by_id[item["EventItemId"]] = entry
        meetings.append(meeting)

    # Two meetings on the same date would collide on /meeting/{date}.html.
    date_counts = Counter(m["date"] for m in meetings)
    for m in meetings:
        if date_counts[m["date"]] > 1:
            m["slug"] = f"{m['date']}-{m['event']['EventId']}"

    # Office terms and contact details, straight from the public record.
    persons = {p["PersonId"]: p for p in load("persons.json")}
    terms_by_person = defaultdict(list)
    for rec in load(f"officerecords_{meta['body_id']}.json"):
        terms_by_person[rec["OfficeRecordPersonId"]].append(rec)
    for terms in terms_by_person.values():
        terms.sort(key=lambda t: t["OfficeRecordStartDate"] or "")

    # Members: everyone who actually appears in a vote record, keyed by PersonId.
    # person_aliases.json merges Legistar's duplicate person records for the
    # same human into one page, transparently.
    aliases = {}
    if ALIASES.exists():
        raw_aliases = json.loads(ALIASES.read_text(encoding="utf-8"))
        aliases = {int(k): v for k, v in raw_aliases.items() if not k.startswith("_")}

    members = {}
    for meeting in meetings:
        for entry in meeting["items"]:
            for vote in entry["votes"]:
                pid = vote["VotePersonId"]
                alias = aliases.get(pid)
                canonical_pid = alias["canonical"] if alias else pid
                member = members.setdefault(
                    canonical_pid,
                    {
                        "person_id": canonical_pid,
                        "name": (alias["canonical_name"] if alias else vote["VotePersonName"].strip()),
                        "slug": None,
                        "votes": [],
                        "merged_from": set(),
                    },
                )
                if alias:
                    member["merged_from"].add(alias["recorded_as"])
                else:
                    member["name"] = member["name"] or vote["VotePersonName"].strip()
                member["votes"].append({"vote": vote, "entry": entry})

    for member in members.values():
        member["slug"] = slugify(member["name"])
        member["votes"].sort(
            key=lambda v: (
                v["entry"]["meeting"]["date"],
                v["entry"]["item"].get("EventItemMinutesSequence") or 0,
            ),
            reverse=True,
        )
        by_year = defaultdict(list)
        for v in member["votes"]:
            by_year[v["entry"]["meeting"]["date"][:4]].append(v)
        member["votes_by_year"] = sorted(by_year.items(), reverse=True)
        member["first_vote"] = member["votes"][-1]["entry"]["meeting"]["date"]
        member["last_vote"] = member["votes"][0]["entry"]["meeting"]["date"]

        terms = terms_by_person.get(member["person_id"], [])
        member["terms"] = [
            {
                "title": t["OfficeRecordTitle"] or "(title not recorded)",
                "start": fmt_date(t["OfficeRecordStartDate"]),
                "end": fmt_date(t["OfficeRecordEndDate"]),
            }
            for t in terms
        ]
        current = [t for t in member["terms"] if t["end"] >= today]
        member["current_title"] = current[-1]["title"] if current else None
        member["is_current"] = bool(current)
        person = persons.get(member["person_id"], {})
        member["email"] = person.get("PersonEmail") or None

    members = sorted(members.values(), key=lambda m: last_name(m["name"]))
    current_members = [m for m in members if m["is_current"]]
    former_members = [m for m in members if not m["is_current"]]

    # Topic index (unofficial layer).
    topics = defaultdict(list)
    for entry in items_by_id.values():
        if entry["category"]:
            topics[entry["category"]].append(entry)
    topic_pages = [
        {
            "name": name,
            "slug": slugify(name),
            "entries": sorted(entries, key=lambda e: e["meeting"]["date"], reverse=True),
            "voted": sum(1 for e in entries if e["votes"]),
        }
        for name, entries in sorted(topics.items())
    ]
    for tp in topic_pages:
        for entry in tp["entries"]:
            entry["category_slug"] = tp["slug"]

    n_votes = sum(len(e["votes"]) for m in meetings for e in m["items"])
    date_range = (meetings[-1]["date"], meetings[0]["date"]) if meetings else ("", "")
    by_year = defaultdict(list)
    for m in meetings:
        by_year[m["date"][:4]].append(m)
    meetings_by_year = sorted(by_year.items(), reverse=True)

    env = Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    common = {
        "legistar": LEGISTAR,
        "repo": REPO,
        "fetched_at": today,
        "date_range": date_range,
    }

    sitemap_paths = []

    def render(template, out_path: Path, **ctx):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        rel = out_path.relative_to(SITE).as_posix()
        sitemap_paths.append(rel)
        html = env.get_template(template).render(
            **common, canonical=f"{BASE_URL}/{rel}", **ctx
        )
        out_path.write_text(html, encoding="utf-8")

    if SITE.exists():
        shutil.rmtree(SITE)
    SITE.mkdir()
    shutil.copy(TEMPLATES / "style.css", SITE / "style.css")
    # Custom-domain marker for GitHub Pages; must survive the site/ wipe.
    (SITE / "CNAME").write_text(f"{CNAME_DOMAIN}\n", encoding="utf-8")
    (SITE / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\n\nSitemap: {BASE_URL}/sitemap.xml\n",
        encoding="utf-8",
    )

    elections_meta, elections = load_elections()
    render("about.html", SITE / "about.html", root="")
    if elections:
        render("elections.html", SITE / "elections.html", root="",
               elections=elections, elections_meta=elections_meta,
               precinct_map=build_precinct_map())
    stats = {"votes": n_votes, "meetings": len(meetings), "members": len(members)}
    election_stats = {
        "contests": sum(len(e["contests"]) for e in elections),
        "elections": len(elections),
    }
    render("index.html", SITE / "index.html", root="",
           stats=stats, election_stats=election_stats)
    render(
        "council.html",
        SITE / "council.html",
        root="",
        current_members=current_members,
        former_members=former_members,
        meetings_by_year=meetings_by_year,
        stats=stats,
        topics=topic_pages,
    )
    for page in [
        {"slug": "house-prices", "name": "House prices",
         "pitch": "What homes in Vienna have sold and been valued at over the years.",
         "source": "Fairfax County assessment and sales records, or a published "
                   "town-level price index (Zillow ZHVI / Redfin)"},
        {"slug": "weather", "name": "Weather",
         "pitch": "High and low temperatures in and around Vienna across the years.",
         "source": "NOAA National Centers for Environmental Information (GHCN "
                   "station records, nearest long-running station)"},
        {"slug": "population", "name": "Population",
         "pitch": "How many people call Vienna home, decade by decade.",
         "source": "U.S. Census Bureau — decennial census and American Community "
                   "Survey for the Town of Vienna (place 51-81072)"},
    ]:
        render("planned.html", SITE / f"{page['slug']}.html", root="", page=page)
    for member in members:
        render("member.html", SITE / "member" / f"{member['slug']}.html", root="../", member=member)
    for meeting in meetings:
        render("meeting.html", SITE / "meeting" / f"{meeting['slug']}.html", root="../", meeting=meeting)
    for entry in items_by_id.values():
        render("item.html", SITE / "item" / f"{entry['item']['EventItemId']}.html", root="../", entry=entry)
    for tp in topic_pages:
        render("topic.html", SITE / "topic" / f"{tp['slug']}.html", root="../", topic=tp)

    sitemap = ['<?xml version="1.0" encoding="UTF-8"?>',
               '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for rel in sitemap_paths:
        sitemap.append(
            f"  <url><loc>{BASE_URL}/{rel}</loc><lastmod>{today}</lastmod></url>"
        )
    sitemap.append("</urlset>")
    (SITE / "sitemap.xml").write_text("\n".join(sitemap) + "\n", encoding="utf-8")

    print(
        f"built site/: {len(members)} members ({len(current_members)} current), "
        f"{len(meetings)} meetings, {len(items_by_id)} items, {n_votes} vote records, "
        f"{len(topic_pages)} topics"
    )


if __name__ == "__main__":
    main()
