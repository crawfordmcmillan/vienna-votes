"""categorize.py — assign an unofficial topic category to every agenda item.

Deterministic and auditable: categories come from (1) transparent title rules
below, then (2) exact-title assignments in topic_assignments.json for items
the rules don't catch. Output is categories.csv (id,date,title,category),
regenerated in full on every run. Titles with no rule and no assignment are
written to uncategorized_titles.txt for review and left out of the CSV.

These categories are this site's own editorial layer. They are displayed as
"unofficial" and never alter the underlying vote records.
"""
import csv
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"
OUT = ROOT / "categories.csv"
ASSIGNMENTS = ROOT / "topic_assignments.json"
UNCATEGORIZED = ROOT / "uncategorized_titles.txt"

# First match wins. Specific topical rules come before generic instrument
# words (contract, award) so "design contract for Southside Park" lands in
# Parks & Recreation, not Contracts & Purchasing.
RULES = [
    (r"^(regular business|public hearings?|rollcall|new business|old business|"
     r"unfinished business|resolutu?ion|approval|presentations?|none|"
     r"no regular business|regular agenda)$", "Procedural"),
    (r"(regular business|public hearings?)$", "Procedural"),
    (r"\b(roll call|invocation|pledge of allegiance|adjourn\w*|closed session|baha|"
     r"receipt of petitions|reports?( and|/| of )|proposals? for additional|"
     r"agenda order|order of business|work session agenda|"
     r"americans with disabilities act standards)\b", "Procedural"),
    (r"\bminutes\b", "Minutes"),
    (r"(^|\b)(approval of the )?(regular( council)? meetings? of .*\d{4}|"
     r"work session of .*\d{4})", "Minutes"),
    (r"\bconsent agenda\b", "Consent Agenda"),
    (r"\b(appoint|appointment|reappoint|resignation|vacancy)\b", "Appointments & Resignations"),
    (r"\b(proclamations?|proclaim|recognition|recognize|commend|honor(ing)?\b|retirement|"
     r"swearing.?in|newly elected|appreciation|awards|presentation (to|for|by) |"
     r"awareness (day|month)|100th birthday|anniversary)\b",
     "Proclamations & Recognitions"),
    (r"\b(tree|trees|canopy|solarize|sustainability|environmental?|PFAS|"
     r"hazardous mitigation|stream restoration|leaf (collection|mulch\w*)|"
     r"leaf mulch|bee city|(community |learning )garden|native plant|"
     r"green streets)\b", "Environment, Trees & Sustainability"),
    (r"\b(alley vacation|vacation of a portion|street vacation|easements?|"
     r"historic property register|final plats?|lot consolidation|"
     r"consolidation of .*lots|boundary line|master sign plan|demolish|"
     r"modification of requirements)\b", "Land Use, Zoning & Development"),
    (r"\b(water|sewers?|stormwater|sanitary|watermain|water main|wastewater|"
     r"solid waste|recycling|refuse|drainage)\b", "Water, Sewer & Stormwater"),
    (r"\b(zoning|rezon\w*|subdivision|site plan|variance|setback|proffer\w*|"
     r"conditional use|special exception|comprehensive plan|land use|"
     r"architectural review|windover|historic district|annexation|lot line|"
     r"maple avenue commercial|\bMAC\b|planned development|text amendments?|"
     r"\bBZA\b)\b", "Land Use, Zoning & Development"),
    (r"\b(park|parks|recreation|community center|ball ?field|playground|"
     r"tennis|trail|pool|caboose|town green|pickleball|aquatics?|"
     r"fitness center)\b", "Parks & Recreation"),
    (r"\b(streets?|sidewalks?|traffic|parking|crosswalk|transportation|paving|"
     r"repaving|intersection|speed (limit|hump)|bike\w*|bicycle|pedestrian|"
     r"road diet|signal|microtransit|roundabout|transit)\b",
     "Streets, Sidewalks & Transportation"),
    (r"\b(police|public safety|emergency|fire (station|department)|"
     r"radio system|body.?worn|computer aided dispatch|in.car video|"
     r"gang task force|asset forfeiture)\b", "Public Safety"),
    (r"\b(town hall|annex|HVAC|elevator|furniture|reception desk|"
     r"property yard|real property|land acquisition|key card)\b",
     "Town Facilities & Property"),
    (r"\b(budget|tax|taxes|CIP|capital improvement|audit\w*|financial|"
     r"fiscal year|bond|revenue|fee schedule|utility rates?|donation|"
     r"spending|invoices?|payment|legal fees|legal services|investment "
     r"(report|policy)|debt management|fund balance|appropriations?|"
     r"carryforwards?|CARES act|ARPA|SLFRF|recovery funds|"
     r"coronavirus relief|fiscal recovery|trust)\b", "Budget, Taxes & Finance"),
    (r"\b(grant|legislative (agenda|priorities|update)|re.?districting|"
     r"fairfax county|regional|VDOT|I-66|opioid|CDBG|"
     r"commonwealth|SB\d+|HB\d+)\b", "Grants & Intergovernmental"),
    (r"\b(economic development|business (permitting|liaison)|"
     r"town business)\b", "Economic Development"),
    (r"\b(strategic plan|council priorities|top priorities|"
     r"meeting (schedule|dates?)|town calendar|electronic participation|"
     r"spending limit|compensation|salar(y|ies)|conflict of interest|"
     r"town attorney|classification|observed holidays|position\b|pension|"
     r"employ(ee|ment) agreement|VRS contribution|leave request|"
     r"survey|continuity of government|establish\w*|bylaws|membership|"
     r"town logo|brand positioning|two.hour limit|town manager)\b",
     "Governance & Administration"),
    (r"\b(festival|oktoberfest|viva.?vienna|halloween|parade|block party|"
     r"church street stroll|community event|first night|teen council|"
     r"public art|arts? (commission|society)|holiday ornament|"
     r"vienna stories|historic vienna|liberty amendments|fireworks|"
     r"independence day|museum|library)\b", "Community, Arts & Events"),
    (r"\b(ordinance|town code|code (amendments?|sections?|provisions?)|"
     r"chapter \d+|section\.? \d+)\b", "Town Code & Ordinances"),
    (r"\b(contract\w*|IFB|RFP|purchase|procurement|award|sole source|"
     r"vehicle|equipment|lease|bid|franchise|memorandum of understanding|"
     r"MOU|license (agreement|renewal)|cooperation agreement|"
     r"service agreement|maintenance (agreement|support)|funding (and|of|with)|"
     r"telecommunication|telephone|VoIP|software|granicus|microsoft|"
     r"encoder|website|ArcGIS|technology|expenditure|road salt|"
     r"salt brine|snow blower|sweeper|truck)\b", "Contracts & Purchasing"),
]
COMPILED = [(re.compile(p, re.IGNORECASE), c) for p, c in RULES]


def categorize(title: str, assignments: dict) -> str | None:
    if title in assignments:
        return assignments[title]
    for pattern, category in COMPILED:
        if pattern.search(title):
            return category
    return None


def main():
    assignments = {}
    if ASSIGNMENTS.exists():
        assignments = json.loads(ASSIGNMENTS.read_text(encoding="utf-8"))

    events = {e["EventId"]: e for e in json.loads((DATA / "events.json").read_text(encoding="utf-8"))}
    rows = []
    missed = Counter()
    for f in sorted(DATA.glob("eventitems_*.json")):
        event_id = int(f.stem.split("_")[1])
        if event_id not in events:
            continue
        date = events[event_id]["EventDate"][:10]
        for item in json.loads(f.read_text(encoding="utf-8")):
            title = (item.get("EventItemTitle") or "").strip()
            if not title:
                category = "Procedural"
            else:
                category = categorize(title, assignments)
            if category:
                rows.append({"id": item["EventItemId"], "date": date, "title": title, "category": category})
            else:
                missed[title] += 1

    rows.sort(key=lambda r: (r["category"], r["date"], r["id"]))
    with OUT.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "date", "title", "category"])
        writer.writeheader()
        writer.writerows(rows)

    if missed:
        UNCATEGORIZED.write_text(
            "\n".join(f"{count}\t{title}" for title, count in missed.most_common()),
            encoding="utf-8",
        )
    elif UNCATEGORIZED.exists():
        UNCATEGORIZED.unlink()

    print(f"categorized {len(rows)} items into {len(set(r['category'] for r in rows))} categories")
    counts = Counter(r["category"] for r in rows)
    for cat, n in counts.most_common():
        print(f"  {n:5d}  {cat}")
    print(f"uncategorized: {sum(missed.values())} items ({len(missed)} distinct titles)"
          + (f" -> {UNCATEGORIZED.name}" if missed else ""))


if __name__ == "__main__":
    main()
