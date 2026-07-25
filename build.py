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
LEGISTAR = "https://vienna-va.legistar.com"
REPO = "https://github.com/crawfordmcmillan/vienna-votes"


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
    members = {}
    for meeting in meetings:
        for entry in meeting["items"]:
            for vote in entry["votes"]:
                pid = vote["VotePersonId"]
                member = members.setdefault(
                    pid,
                    {
                        "person_id": pid,
                        "name": vote["VotePersonName"].strip(),
                        "slug": None,
                        "votes": [],
                    },
                )
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

    def render(template, out_path: Path, **ctx):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        html = env.get_template(template).render(**common, **ctx)
        out_path.write_text(html, encoding="utf-8")

    if SITE.exists():
        shutil.rmtree(SITE)
    SITE.mkdir()
    shutil.copy(TEMPLATES / "style.css", SITE / "style.css")
    # Custom-domain marker for GitHub Pages; must survive the site/ wipe.
    (SITE / "CNAME").write_text("viennavotes.org\n", encoding="utf-8")

    render("about.html", SITE / "about.html", root="")
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

    print(
        f"built site/: {len(members)} members ({len(current_members)} current), "
        f"{len(meetings)} meetings, {len(items_by_id)} items, {n_votes} vote records, "
        f"{len(topic_pages)} topics"
    )


if __name__ == "__main__":
    main()
