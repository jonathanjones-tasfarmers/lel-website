"""
events_feed.py - builds events-feed.xml (Atom) from the public LEL_Events_View layer, so that a standard
Power Automate RSS trigger ("When a feed item is published") can email people on the registration list
whenever a new event becomes ready to announce.

An event is announced once it is:
  * in the public view (Status = Published, so drafts never appear),
  * not cancelled,
  * has a Humanitix (or other https) registration link (the site promises notification when events "open for
    registration"; an event with no link yet is picked up on the day the link is added),
  * and has not already finished (its last day is today or later, Hobart time).

State: events-feed-state.json maps each event's globalid to the moment it first became announceable. That moment
is the entry's published date, which is what the RSS trigger keys on. The FIRST run seeds every event already
announceable with its creation date, so nothing old is re-announced; after that, a newly announceable event is
stamped with the current time. The feed's own updated time is the newest published time, so an unchanged feed
produces no file change and no commit.

Run:  python3 scripts/events_feed.py [--out events-feed.xml] [--state events-feed-state.json]
Needs nothing but the Python standard library.
"""
import json, os, sys, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

SERVICE = "https://services6.arcgis.com/RkRGg1mAki7yG3EG/arcgis/rest/services/LEL_Events_View/FeatureServer/0/query"
SITE = "https://jonathanjones-tasfarmers.github.io/lel-website/"
FEED_URL = SITE + "events-feed.xml"
EVENTS_PAGE = "https://lel-indie520d1a051c0.hub.arcgis.com/pages/f81fbf4601c441d988b183c84bab6214"
HOBART = ZoneInfo("Australia/Hobart")
F = dict(title="untitled_question_1", type="type_of_event", date="_date", time="untitled_question_4", location="untitled_question_5",
         end="untitled_question_18", travel="untitled_question_6", desc="untitled_question_7", audience="untitled_question_8", bring="untitled_question_9",
         with_="untitled_question_10", free="is_it_free", seats="number_of_places", status="untitled_question_14", link="humanitix_link")


def arg(name, default):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def service_date(v):
    """Same rule as page-8-events.html: a date at UTC midnight is that calendar date, otherwise the Hobart date."""
    if isinstance(v, str) and len(v) >= 10 and v[4] == "-":
        return v[:10]
    if not isinstance(v, (int, float)):
        return ""
    d = datetime.fromtimestamp(v / 1000, tz=timezone.utc)
    if d.hour == 0 and d.minute == 0:
        return d.strftime("%Y-%m-%d")
    return d.astimezone(HOBART).strftime("%Y-%m-%d")


def pretty(d):
    x = datetime.strptime(d, "%Y-%m-%d")
    return x.strftime("%A ") + str(x.day) + x.strftime(" %B %Y")


def fetch():
    q = urllib.parse.urlencode({"where": "1=1", "outFields": "*", "f": "json", "resultRecordCount": 500})
    with urllib.request.urlopen(SERVICE + "?" + q, timeout=30) as r:
        data = json.load(r)
    if "error" in data:
        raise RuntimeError(data["error"])
    return [f["attributes"] for f in data.get("features", [])]


def announceable(a, today):
    if str(a.get(F["status"]) or "").strip().lower() == "cancelled":
        return None
    link = str(a.get(F["link"]) or "").strip()
    if not link.startswith("https://"):
        return None
    d, e = service_date(a.get(F["date"])), service_date(a.get(F["end"]))
    if not d or (e or d) < today:
        return None
    gid = str(a.get("globalid") or a.get("GlobalID") or a.get("objectid"))
    return dict(id=gid, title=str(a.get(F["title"]) or "").strip(), type=str(a.get(F["type"]) or "").strip(), date=d, end=e, time=str(a.get(F["time"]) or "").strip(),
                location=str(a.get(F["location"]) or "").strip(), travel=str(a.get(F["travel"]) or "").strip(), desc=str(a.get(F["desc"]) or "").strip(),
                audience=str(a.get(F["audience"]) or "").strip(), bring=str(a.get(F["bring"]) or "").strip(), with_=str(a.get(F["with_"]) or "").strip(),
                free=not str(a.get(F["free"]) or "").strip().lower().startswith(("no", "n ", "false", "paid")), seats=a.get(F["seats"]), link=link,
                created=a.get("CreationDate"))


def when_text(ev):
    s = pretty(ev["date"]) + (" to " + pretty(ev["end"]) if ev["end"] and ev["end"] != ev["date"] else "")
    return s + (", " + ev["time"] if ev["time"] else "")


def plain_summary(ev):
    bits = [when_text(ev)]
    if ev["location"]:
        bits.append(ev["location"])
    bits.append("Free" if ev["free"] else "Paid event")
    out = " | ".join(bits)
    return out + ". " + ev["desc"] if ev["desc"] else out


def html_body(ev):
    rows = [("When", when_text(ev)), ("Where", ev["location"] + (" (" + ev["travel"] + ")" if ev["travel"] else "")), ("Cost", "Free" if ev["free"] else "Paid event"),
            ("Who it's for", ev["audience"]), ("What to bring", ev["bring"]), ("Presented with", ev["with_"])]
    if ev["seats"]:
        rows.append(("Places", str(int(ev["seats"])) if float(ev["seats"]).is_integer() else str(ev["seats"])))
    t = "".join(f"<tr><td style=\"padding:4px 12px 4px 0;color:#5b6b78\"><b>{escape(k)}</b></td><td style=\"padding:4px 0\">{escape(v)}</td></tr>" for k, v in rows if v.strip())
    return (f"<p><b>{escape(ev['type'])}</b></p><p>{escape(ev['desc'])}</p><table>{t}</table>"
            f"<p><a href=\"{escape(ev['link'], {chr(34): '&quot;'})}\">Register for this event</a></p>")


def main():
    out, state_path = arg("--out", "events-feed.xml"), arg("--state", "events-feed-state.json")
    now = datetime.now(timezone.utc)
    today = now.astimezone(HOBART).strftime("%Y-%m-%d")
    rows = [announceable(a, today) for a in fetch()]
    events = {e["id"]: e for e in rows if e}
    first_run = not os.path.exists(state_path)
    state = {} if first_run else json.load(open(state_path))
    iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")
    for gid, e in events.items():
        if gid not in state:
            seed = datetime.fromtimestamp(e["created"] / 1000, tz=timezone.utc) if (first_run and e["created"]) else now
            state[gid] = iso(seed)
            print(("Seeded (old, will not be announced): " if first_run else "NEW, will be announced: ") + e["title"])
    state = {g: t for g, t in state.items() if g in events}      # drop finished / cancelled / unlinked events
    json.dump(state, open(state_path, "w"), indent=1, sort_keys=True); open(state_path, "a").write("\n")
    ordered = sorted(events.values(), key=lambda e: state[e["id"]], reverse=True)[:50]
    updated = max(state.values()) if state else iso(now)
    entries = ""
    for e in ordered:
        entries += (f"  <entry>\n    <id>urn:lel-event:{escape(e['id'])}</id>\n    <title>{escape(e['title'])}</title>\n"
                    f"    <link rel=\"alternate\" href=\"{escape(e['link'], {chr(34): '&quot;'})}\"/>\n    <published>{state[e['id']]}</published>\n    <updated>{state[e['id']]}</updated>\n"
                    f"    <category term=\"{escape(e['type'] or 'Event', {chr(34): '&quot;'})}\"/>\n    <summary type=\"text\">{escape(plain_summary(e))}</summary>\n"
                    f"    <content type=\"html\">{escape(html_body(e))}</content>\n  </entry>\n")
    xml = (f"<?xml version=\"1.0\" encoding=\"utf-8\"?>\n<feed xmlns=\"http://www.w3.org/2005/Atom\">\n  <title>Low Emissions Livestock: new events</title>\n"
           f"  <subtitle>Events that are open for registration. Used to email people who asked to be notified.</subtitle>\n  <id>{FEED_URL}</id>\n"
           f"  <link rel=\"self\" href=\"{FEED_URL}\"/>\n  <link rel=\"alternate\" href=\"{EVENTS_PAGE}\"/>\n  <updated>{updated}</updated>\n{entries}</feed>\n")
    old = open(out).read() if os.path.exists(out) else ""
    if xml != old:
        open(out, "w").write(xml)
    print(f"{len(ordered)} event(s) in the feed; feed {'changed' if xml != old else 'unchanged'}.")


if __name__ == "__main__":
    main()
