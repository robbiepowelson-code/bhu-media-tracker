#!/usr/bin/env python3
"""
BHU Media Tracker — daily scanner.

Pulls news + Reddit feeds, filters for Berkeley/Oakland homelessness and
street-vendor coverage, extracts reporter bylines, and maintains the press list:

  * reporters with a fresh byline are marked active (and added if new)
  * reporters with no byline for > 6 months are moved to the Removed tab
    (individual reporters only — desks/advocacy/gov contacts are flagged, not removed)

Stdlib only — no pip dependencies. Run from repo root:  python3 scanner/scan.py
Offline test mode:  python3 scanner/scan.py --offline-test scanner/fixtures
"""

import hashlib
import html
import json
import os
import re
import smtplib
import ssl
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import parsedate_to_datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
CONFIG = json.load(open(os.path.join(ROOT, "scanner", "config.json")))

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
      "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
ATOM = "{http://www.w3.org/2005/Atom}"
DC = "{http://purl.org/dc/elements/1.1/}"
MAX_ARTICLE_FETCHES = 25

OFFLINE_DIR = None
if len(sys.argv) > 2 and sys.argv[1] == "--offline-test":
    OFFLINE_DIR = sys.argv[2]


# ---------------------------------------------------------------- utilities

def today():
    return datetime.now(timezone.utc).date()


def norm_name(name):
    if not name:
        return ""
    n = re.sub(r"[^a-z .'-]", "", name.lower().strip())
    return re.sub(r"\s+", " ", n)


def norm_outlet(outlet):
    if not outlet:
        return None
    key = outlet.lower().strip()
    return CONFIG["outlet_aliases"].get(key, outlet.strip())


def fetch(url, timeout=25, tries=3):
    """Fetch a URL with retry/backoff. In offline test mode, serve from fixtures."""
    if OFFLINE_DIR:
        fmap = json.load(open(os.path.join(OFFLINE_DIR, "url_map.json")))
        path = fmap.get(url)
        if not path:
            raise IOError(f"offline: no fixture for {url}")
        return open(os.path.join(OFFLINE_DIR, path), "rb").read()
    headers = dict(UA)
    if "reddit.com" in url:
        time.sleep(15)  # unauthenticated Reddit is aggressively rate-limited
        headers["User-Agent"] = "linux:bhu-media-tracker:v1.0 (nonprofit news monitor)"
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 500, 502, 503, 504) and attempt < tries - 1:
                time.sleep(15 if "reddit.com" in url else 6 * (attempt + 1))
                continue
            raise
        except Exception as e:
            last = e
            if attempt < tries - 1:
                time.sleep(4)
                continue
            raise
    raise last


def text_of(el):
    return html.unescape("".join(el.itertext())).strip() if el is not None else ""


def parse_date(s):
    if not s:
        return None
    try:
        return parsedate_to_datetime(s).date()
    except Exception:
        pass
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return datetime(int(m[1]), int(m[2]), int(m[3])).date()
    return None


# ---------------------------------------------------------------- feed parsing

def parse_feed(raw):
    """Return list of entries from RSS 2.0 or Atom bytes."""
    root = ET.fromstring(raw)
    entries = []
    for item in root.findall(".//item"):  # RSS
        e = {
            "title": text_of(item.find("title")),
            "link": text_of(item.find("link")),
            "summary": re.sub(r"<[^>]+>", " ", text_of(item.find("description")))[:600],
            "date": parse_date(text_of(item.find("pubDate"))),
            "authors": [],
            "source_outlet": None,
        }
        creator = item.find(DC + "creator")
        if creator is not None:
            e["authors"] = split_authors(text_of(creator))
        if not e["authors"]:
            au = item.find("author")  # BLOX CMS style: "email@site.org (Jane Doe)"
            if au is not None:
                m = re.search(r"\(([^)]+)\)", text_of(au))
                if m:
                    e["authors"] = split_authors(m.group(1))
        src = item.find("source")
        if src is not None:
            e["source_outlet"] = text_of(src)
        entries.append(e)
    for item in root.findall(ATOM + "entry"):  # Atom (Reddit)
        link_el = item.find(ATOM + "link")
        author_el = item.find(ATOM + "author/" + ATOM + "name")
        e = {
            "title": text_of(item.find(ATOM + "title")),
            "link": link_el.get("href") if link_el is not None else "",
            "summary": re.sub(r"<[^>]+>", " ", text_of(item.find(ATOM + "content")))[:600],
            "date": parse_date(text_of(item.find(ATOM + "updated")) or text_of(item.find(ATOM + "published"))),
            "authors": [text_of(author_el)] if author_el is not None else [],
            "source_outlet": None,
        }
        entries.append(e)
    return entries


NON_PERSON = re.compile(
    r"staff|desk|editor|newsroom|associated press|bay city news|city news service|"
    r"contributor|correspondent$|team$|report$|news$|media$|admin|www\.|\.com|https?:",
    re.I,
)


def split_authors(s):
    """'By Jane Doe, John Roe and A. B.' -> ['Jane Doe', 'John Roe', 'A. B.']"""
    if not s:
        return []
    s = re.sub(r"^\s*by\s+", "", s.strip(), flags=re.I)
    parts = re.split(r",| and | & |;|\|", s)
    out = []
    for p in parts:
        p = re.sub(r"\(.*?\)", "", p).strip(" .–-")
        if not p or len(p) > 60 or NON_PERSON.search(p):
            continue
        if len(p.split()) < 2:  # single tokens are rarely a usable byline
            continue
        out.append(p)
    return out


# ------------------------------------------------------- article byline lookup

META_AUTHOR_PATTERNS = [
    r'<meta[^>]+name=["\']author["\'][^>]+content=["\']([^"\']+)["\']',
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']author["\']',
    r'<meta[^>]+name=["\']parsely-author["\'][^>]+content=["\']([^"\']+)["\']',
    r'<meta[^>]+name=["\']sailthru\.author["\'][^>]+content=["\']([^"\']+)["\']',
    r'<meta[^>]+property=["\']article:author["\'][^>]+content=["\'](?!https?:)([^"\']+)["\']',
]


GOOGLEISH = re.compile(r"//(?:[\w.-]*\.)?(google(?:usercontent)?\.com|gstatic\.com|googleapis\.com|blogger\.com|youtube\.com)")


def resolve_google_link(url):
    """Google News RSS links are redirect pages — dig out the real article URL."""
    if "news.google.com" not in url:
        return url
    try:
        raw = fetch(url, timeout=15)[:300_000].decode("utf-8", "ignore")
    except Exception:
        return url
    for m in re.finditer(r'href="(https?://[^"]+)"', raw):
        u = html.unescape(m.group(1))
        if not GOOGLEISH.search(u) and "news.google" not in u:
            return u
    m = re.search(r'data-n-au="(https?://[^"]+)"', raw)
    return html.unescape(m.group(1)) if m else url


def bylines_from_article(url):
    """Best-effort byline extraction from an article page (meta tags + JSON-LD)."""
    try:
        raw = fetch(url, timeout=20)[:400_000].decode("utf-8", "ignore")
    except Exception:
        return []
    found = []
    for pat in META_AUTHOR_PATTERNS:
        for m in re.finditer(pat, raw, re.I):
            found += split_authors(html.unescape(m.group(1)))
    for m in re.finditer(r'"author"\s*:\s*(\[.*?\]|\{.*?\})', raw, re.S):  # JSON-LD
        blob = m.group(1)
        for nm in re.finditer(r'"name"\s*:\s*"([^"]+)"', blob):
            found += split_authors(html.unescape(nm.group(1)))
    seen, out = set(), []
    for a in found:
        k = norm_name(a)
        if k and k not in seen:
            seen.add(k)
            out.append(a)
    return out[:4]


# ---------------------------------------------------------------- relevance

def relevant(entry, feed):
    text = f"{entry['title']} {entry['summary']}".lower()
    topic_ok = any(k in text for k in CONFIG["topic_keywords"])
    geo_ok = any(k in text for k in CONFIG["geo_keywords"])
    if feed.get("requires_topic") and not topic_ok:
        return None
    if feed.get("requires_geo") and not geo_ok:
        return None
    if not feed.get("requires_topic") and not feed.get("requires_geo"):
        # pre-scoped feeds (Google News queries, Reddit searches, Street Spirit)
        if feed["kind"] == "social" and not topic_ok:
            return None
    matched = sorted({k for k in CONFIG["topic_keywords"] if k in text})
    strong = any(k in text for k in CONFIG["strong_keywords"])
    return {"topics": matched, "notable": strong}


# ---------------------------------------------------------------- main scan

def article_id(entry):
    basis = (entry.get("link") or "") + "|" + re.sub(r"\W+", "", entry["title"].lower())[:80]
    return hashlib.sha1(basis.encode()).hexdigest()[:16]


def google_news_split(title):
    """Google News titles look like 'Headline - Outlet'."""
    m = re.match(r"^(.*)\s[-–]\s([^-–]{2,60})$", title)
    return (m.group(1).strip(), m.group(2).strip()) if m else (title, None)


def run_scan():
    press = json.load(open(os.path.join(DATA, "press_list.json")))
    try:
        coverage = json.load(open(os.path.join(DATA, "coverage.json")))
    except FileNotFoundError:
        coverage = {"articles": []}

    known_urls = {a["url"] for a in coverage["articles"]}
    known_ids = {a["id"] for a in coverage["articles"]}
    known_titles = {re.sub(r"\W+", "", a["title"].lower())[:80] for a in coverage["articles"]}
    feed_log, new_articles = [], []
    fetches_left = MAX_ARTICLE_FETCHES

    for feed in CONFIG["feeds"]:
        status = {"feed": feed["name"], "ok": False, "entries": 0, "matched": 0, "error": None}
        try:
            entries = parse_feed(fetch(feed["url"]))
            status["ok"], status["entries"] = True, len(entries)
        except Exception as e:
            status["error"] = f"{type(e).__name__}: {e}"[:200]
            feed_log.append(status)
            continue

        for e in entries:
            rel = relevant(e, feed)
            if not rel or not e.get("link"):
                continue
            # ignore archive/featured items — only fresh coverage counts
            if e.get("date") and (today() - e["date"]).days > 90:
                continue
            title, outlet = e["title"], feed.get("outlet")
            if feed["kind"] == "aggregator":
                title, gn_outlet = google_news_split(e["title"])
                outlet = gn_outlet or e.get("source_outlet") or outlet
            elif e.get("source_outlet"):
                outlet = e["source_outlet"]
            if not outlet:  # derive from the article's domain (e.g. Bing items)
                m = re.search(r"https?://(?:www\.)?([^/]+)/", e["link"] + "/")
                outlet = m.group(1) if m else None
            outlet = norm_outlet(outlet)

            aid = article_id({"link": e["link"], "title": title})
            tkey = re.sub(r"\W+", "", title.lower())[:80]
            if e["link"] in known_urls or aid in known_ids or (tkey and tkey in known_titles):
                continue

            link = e["link"]
            if feed["kind"] == "aggregator" and "news.google.com" in link and fetches_left > 0:
                fetches_left -= 1
                real = resolve_google_link(link)
                if real != link and real not in known_urls:
                    link = real
            authors = e["authors"]
            if not authors and feed["kind"] != "social" and fetches_left > 0:
                fetches_left -= 1
                authors = bylines_from_article(link)

            art = {
                "id": aid,
                "title": title,
                "url": link,
                "outlet": outlet,
                "authors": authors,
                "date": str(e["date"] or today()),
                "topics": rel["topics"],
                "notable": rel["notable"],
                "kind": "social" if feed["kind"] == "social" else "news",
                "feed": feed["name"],
                "found_on": str(today()),
                "summary": e["summary"][:300],
            }
            coverage["articles"].append(art)
            new_articles.append(art)
            known_urls.add(e["link"])
            known_urls.add(link)
            known_ids.add(aid)
            known_titles.add(tkey)
            status["matched"] += 1
        feed_log.append(status)

    # retention window
    cutoff = today() - timedelta(days=CONFIG["coverage_retention_days"])
    coverage["articles"] = [a for a in coverage["articles"] if parse_date(a["date"]) and parse_date(a["date"]) >= cutoff]
    coverage["articles"].sort(key=lambda a: a["date"], reverse=True)
    coverage["articles"] = coverage["articles"][: CONFIG["max_articles_kept"]]
    coverage["updated"] = str(today())

    changes = update_press_list(press, new_articles)
    press["generated"] = str(today())

    json.dump(coverage, open(os.path.join(DATA, "coverage.json"), "w"), indent=1, ensure_ascii=False)
    json.dump(press, open(os.path.join(DATA, "press_list.json"), "w"), indent=1, ensure_ascii=False)
    write_public_snapshot(coverage, press)
    json.dump(
        {"ran": datetime.now(timezone.utc).isoformat(timespec="seconds"),
         "feeds": feed_log, "new_articles": len(new_articles), "changes": changes},
        open(os.path.join(DATA, "scan_log.json"), "w"), indent=1)

    send_digest(new_articles, changes, feed_log)
    print(f"scan done: {len(new_articles)} new articles; changes: {json.dumps(changes)}")
    for s in feed_log:
        flag = "OK " if s["ok"] else "ERR"
        print(f"  [{flag}] {s['feed']}: {s['entries']} entries, {s['matched']} matched {s['error'] or ''}")


# ------------------------------------------------------- press-list maintenance

def update_press_list(press, new_articles):
    rule = CONFIG["removal_rule"]
    changes = {"bylines_updated": [], "added": [], "restored": [], "removed": [], "flagged": []}
    by_name = {}
    for c in press["contacts"]:
        if c.get("name"):
            by_name.setdefault(norm_name(c["name"]), c)
    removed_by_name = {norm_name(r["name"]): r for r in press["removed"] if r.get("name")}

    for art in new_articles:
        if art["kind"] == "social":
            continue
        for author in art["authors"]:
            key = norm_name(author)
            if not key:
                continue
            c = by_name.get(key)
            if c is None and key in removed_by_name:  # reporter came back — restore
                r = removed_by_name.pop(key)
                press["removed"] = [x for x in press["removed"] if norm_name(x.get("name") or "") != key]
                c = {
                    "id": r.get("email") or key,
                    "name": r["name"], "outlet": r.get("outlet"), "email": r.get("email"),
                    "category": "Reporter / journalist", "beat": None, "phone": None,
                    "status": "active", "status_note": "restored — new byline found",
                    "deliverability_note": None, "relevance": None,
                    "source": "auto_scan_restore", "added": str(today()),
                    "last_byline": None, "last_byline_url": None, "byline_count": 0,
                    "seeded": False,
                }
                press["contacts"].append(c)
                by_name[key] = c
                changes["restored"].append(f"{r['name']} ({r.get('outlet')})")
            if c is None:  # brand-new reporter on the beat
                c = {
                    "id": key.replace(" ", "_"),
                    "name": author, "outlet": art["outlet"], "email": None,
                    "category": "Reporter / journalist",
                    "beat": "auto: " + ", ".join(art["topics"][:3]) if art["topics"] else None,
                    "phone": None, "status": "new",
                    "status_note": "auto-added from byline — needs contact info",
                    "deliverability_note": None, "relevance": None,
                    "source": "auto_scan", "added": str(today()),
                    "last_byline": None, "last_byline_url": None, "byline_count": 0,
                    "seeded": False,
                }
                press["contacts"].append(c)
                by_name[key] = c
                changes["added"].append(f"{author} ({art['outlet']}) — {art['title'][:70]}")
            # update byline recency
            prev = c.get("last_byline")
            if not prev or art["date"] > prev:
                c["last_byline"] = art["date"]
                c["last_byline_url"] = art["url"]
            c["byline_count"] = c.get("byline_count", 0) + 1
            if c["status"] in ("unverified", "stale-warning", "dormant"):
                c["status"] = "active"
                c["status_note"] = "verified by byline " + art["date"]
            if c["status"] == "active" or c["status"] == "new":
                changes["bylines_updated"].append(f"{c['name']} — {art['title'][:70]}")
            if art["outlet"] and c.get("outlet") and norm_outlet(art["outlet"]) != norm_outlet(c["outlet"]):
                c["status_note"] = f"byline seen at {art['outlet']} (listed outlet: {c['outlet']})"

    # staleness / removal pass
    keep = []
    for c in press["contacts"]:
        lb = parse_date(c.get("last_byline"))
        tracked_since = parse_date(c.get("added")) or today()
        is_reporter = (c.get("category") or "") in rule["auto_remove_categories"]
        age = (today() - lb).days if lb else (today() - tracked_since).days

        if is_reporter and lb and age > rule["stale_days"]:
            press["removed"].append({
                "name": c.get("name"), "outlet": c.get("outlet"), "email": c.get("email"),
                "reason": f"No byline in {age} days (last: {c['last_byline']}) — 6-month rule",
                "removed_on": str(today()), "source": "auto_rule",
            })
            changes["removed"].append(f"{c.get('name')} ({c.get('outlet')}) — last byline {c['last_byline']}")
            continue
        if is_reporter and lb and age > rule["warn_days"] and c["status"] != "stale-warning":
            c["status"] = "stale-warning"
            c["status_note"] = f"no byline in {age} days — will be removed at {rule['stale_days']}"
            changes["flagged"].append(f"{c.get('name')} ({c.get('outlet')}) — {age} days since byline")
        if not lb and age > rule["stale_days"] and c["status"] in ("unverified", "new") :
            c["status"] = "dormant"
            c["status_note"] = "no byline observed since tracking began — verify manually"
            changes["flagged"].append(f"{c.get('name') or c.get('email')} ({c.get('outlet')}) — never seen, marked dormant")
        keep.append(c)
    press["contacts"] = keep
    return changes


def write_public_snapshot(coverage, press):
    """Sanitized public feed (no emails/phones) served at /feed/data.json —
    consumed by the public Media Tracker page on the BHU Streamlit site."""
    pub_dir = os.path.join(ROOT, "feed")
    os.makedirs(pub_dir, exist_ok=True)
    out = {
        "updated": str(today()),
        "articles": [
            {k: a.get(k) for k in ("title", "url", "outlet", "authors", "date", "topics", "notable", "kind")}
            for a in coverage["articles"]
        ],
        "reporters": [
            {
                "name": c.get("name"), "outlet": c.get("outlet"), "beat": c.get("beat"),
                "category": c.get("category"), "status": c.get("status"),
                "last_byline": c.get("last_byline"), "byline_count": c.get("byline_count", 0),
            }
            for c in press["contacts"]
        ],
        "removed": [
            {"name": r.get("name"), "outlet": r.get("outlet"), "reason": r.get("reason"),
             "removed_on": r.get("removed_on")}
            for r in press["removed"]
        ],
    }
    json.dump(out, open(os.path.join(pub_dir, "data.json"), "w"), indent=1, ensure_ascii=False)


# ---------------------------------------------------------------- email digest

def send_digest(new_articles, changes, feed_log):
    addr = os.environ.get("GMAIL_ADDRESS")
    pw = os.environ.get("GMAIL_APP_PASSWORD")
    to = os.environ.get("DIGEST_TO", addr)
    if not addr or not pw:
        print("digest: GMAIL_ADDRESS / GMAIL_APP_PASSWORD not set — skipping email")
        return
    news = [a for a in new_articles if a["kind"] == "news"]
    social = [a for a in new_articles if a["kind"] == "social"]
    any_changes = any(changes.values())
    if not new_articles and not any_changes:
        print("digest: nothing new — no email sent")
        return

    def sec(title, rows):
        if not rows:
            return ""
        lis = "".join(f"<li>{r}</li>" for r in rows)
        return f"<h3 style='margin:16px 0 6px'>{title}</h3><ul>{lis}</ul>"

    art_rows = [
        f"<a href='{a['url']}'>{html.escape(a['title'])}</a> — {html.escape(a['outlet'] or '?')}"
        + (f" · by {html.escape(', '.join(a['authors']))}" if a["authors"] else "")
        + (" <b>[notable]</b>" if a.get("notable") else "")
        for a in news
    ]
    soc_rows = [f"<a href='{a['url']}'>{html.escape(a['title'])}</a> — {a['outlet']}" for a in social]
    dead = [s["feed"] for s in feed_log if not s["ok"]]

    body = (
        f"<div style='font-family:Georgia,serif;max-width:640px'>"
        f"<h2>BHU Media Tracker — daily digest, {today()}</h2>"
        + sec(f"New coverage ({len(news)})", art_rows)
        + sec(f"Social chatter ({len(soc_rows)})", soc_rows)
        + sec("Reporters auto-added", [html.escape(x) for x in changes["added"]])
        + sec("Reporters restored", [html.escape(x) for x in changes["restored"]])
        + sec("Removed (6-month rule)", [html.escape(x) for x in changes["removed"]])
        + sec("Flagged / dormant", [html.escape(x) for x in changes["flagged"]])
        + sec("Feeds having trouble", [html.escape(x) for x in dead])
        + "<p style='color:#777;font-size:12px'>Full dashboard: your GitHub Pages site.</p></div>"
    )
    msg = EmailMessage()
    msg["Subject"] = f"[BHU Media] {len(news)} new stories, {len(changes['added'])} reporters added — {today()}"
    msg["From"] = addr
    msg["To"] = to
    msg.set_content("HTML email — open in an HTML-capable client.")
    msg.add_alternative(body, subtype="html")
    if OFFLINE_DIR:
        open(os.path.join(DATA, "digest_preview.html"), "w").write(body)
        print("digest: offline mode — wrote data/digest_preview.html instead of sending")
        return
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as s:
        s.login(addr, pw)
        s.send_message(msg)
    print(f"digest: emailed {to}")


if __name__ == "__main__":
    run_scan()
