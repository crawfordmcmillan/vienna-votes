"""build.py — render static HTML from data/ into site/. No network calls.

Reads the raw JSON cached by fetch.py and writes five page types:
index, one page per council member, one per meeting, one per agenda item,
and one per topic (if categories.csv exists). VoteValueName is carried
verbatim everywhere; topic categories are this site's own unofficial layer,
kept in categories.csv and labeled as such.
"""
import csv
import json
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

    render("about.html", SITE / "about.html", root="")
    elections_meta, elections = load_elections()
    if elections:
        render("elections.html", SITE / "elections.html", root="",
               elections=elections, elections_meta=elections_meta)
    stats = {"votes": n_votes, "meetings": len(meetings), "members": len(members)}
    render(
        "index.html",
        SITE / "index.html",
        root="",
        current_members=current_members,
        former_members=former_members,
        meetings_by_year=meetings_by_year,
        stats=stats,
        topics=topic_pages,
    )
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
