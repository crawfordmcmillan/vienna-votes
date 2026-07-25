"""build.py — render static HTML from data/ into site/. No network calls.

Reads the raw JSON cached by fetch.py and writes four page types:
index, one page per council member, one per meeting, one per agenda item.
VoteValueName is carried verbatim everywhere; no scores, no interpretation.
"""
import json
import re
import shutil
from collections import Counter
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).parent
DATA = ROOT / "data"
SITE = ROOT / "site"
TEMPLATES = ROOT / "templates"
LEGISTAR = "https://vienna-va.legistar.com"


def load(name):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")


def matter_url(item) -> str | None:
    if item.get("EventItemMatterId") and item.get("EventItemMatterGuid"):
        return (
            f"{LEGISTAR}/LegislationDetail.aspx"
            f"?ID={item['EventItemMatterId']}&GUID={item['EventItemMatterGuid']}"
        )
    return None


def main():
    meta = load("fetch_meta.json")
    events = sorted(load("events.json"), key=lambda e: e["EventDate"], reverse=True)

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
                "source_url": matter_url(item) or event["EventInSiteURL"],
                "meeting": meeting,
            }
            meeting["items"].append(entry)
            items_by_id[item["EventItemId"]] = entry
        meetings.append(meeting)

    # Two meetings on the same date would collide on /meeting/{date}.html.
    date_counts = Counter(m["date"] for m in meetings)
    for m in meetings:
        if date_counts[m["date"]] > 1:
            m["slug"] = f"{m['date']}-{m['event']['EventId']}"

    # Members: everyone who actually appears in a vote record, keyed by PersonId.
    members = {}
    for meeting in meetings:
        for entry in meeting["items"]:
            for vote in entry["votes"]:
                pid = vote["VotePersonId"]
                member = members.setdefault(
                    pid,
                    {"name": vote["VotePersonName"].strip(), "slug": None, "votes": []},
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
    def last_name(name: str) -> str:
        suffixes = {"jr", "sr", "ii", "iii", "iv"}
        parts = [p for p in name.replace(",", "").split() if p.rstrip(".").lower() not in suffixes]
        return parts[-1] if parts else name

    members = sorted(members.values(), key=lambda m: last_name(m["name"]))

    voted_meetings = [m for m in meetings if any(e["votes"] for e in m["items"])]
    date_range = (meetings[-1]["date"], meetings[0]["date"]) if meetings else ("", "")

    env = Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    common = {
        "legistar": LEGISTAR,
        "fetched_at": meta["fetched_at_utc"][:10],
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

    render("index.html", SITE / "index.html", root="", members=members, meetings=meetings)
    for member in members:
        render(
            "member.html",
            SITE / "member" / f"{member['slug']}.html",
            root="../",
            member=member,
        )
    for meeting in meetings:
        render(
            "meeting.html",
            SITE / "meeting" / f"{meeting['slug']}.html",
            root="../",
            meeting=meeting,
        )
    for entry in items_by_id.values():
        render(
            "item.html",
            SITE / "item" / f"{entry['item']['EventItemId']}.html",
            root="../",
            entry=entry,
        )

    n_votes = sum(len(e["votes"]) for m in meetings for e in m["items"])
    print(
        f"built site/: {len(members)} members, {len(meetings)} meetings "
        f"({len(voted_meetings)} with recorded votes), {len(items_by_id)} items, "
        f"{n_votes} vote records"
    )


if __name__ == "__main__":
    main()
