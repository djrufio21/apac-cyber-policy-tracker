#!/usr/bin/env python3
"""APAC Cyber Policy Tracker.

  python tracker.py            build the page (docs/) and the Markdown copy (briefs/)
  python tracker.py --digest   also write digest.md, the body of the weekly GitHub issue

Reads sources.yml and data/consultations.yml.
"""

import calendar
import datetime as dt
import difflib
import html
import re
import socket
import sys
import urllib.parse
from pathlib import Path
from zoneinfo import ZoneInfo

import feedparser
import yaml

ROOT = Path(__file__).resolve().parent
DOCS, BRIEFS, DATA = ROOT / "docs", ROOT / "briefs", ROOT / "data"
MYT = ZoneInfo("Asia/Kuala_Lumpur")
USER_AGENT = "Mozilla/5.0 (compatible; APACCyberPolicyTracker/1.0)"
SIMILARITY = 0.80
TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")
MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec"
DATE_WORDS = (rf"(\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{MONTHS})[a-z]*\.?(?:,?\s+\d{{4}})?"
              rf"|(?:{MONTHS})[a-z]*\.?\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?)")
CLOSING_RE = re.compile(
    rf"(?:until|by|before|closes?(?:\s+on)?|closing(?:\s+date)?(?:\s+is)?|deadline(?:\s+is|\s+of)?|ends?\s+on|due)"
    rf"\s*:?\s+{DATE_WORDS}", re.I)
STOPWORDS = set("the a an and or of for to in on at by with from new over after amid via its as is are was "
                "says said will to be has have".split())

socket.setdefaulttimeout(25)


# ---------- helpers ----------

def clean(raw):
    text = TAG_RE.sub(" ", raw or "")
    return SPACE_RE.sub(" ", html.unescape(html.unescape(text))).strip()


def shorten(text, limit=240):
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0].rstrip(",.;:") + "…"


def norm(text):
    return re.sub(r"[^a-z0-9 ]", "", (text or "").lower())


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def rx(pattern):
    return re.compile(r"\b(?:" + pattern + r")", re.I)


def google_url(query):
    q = f"{query} when:7d"
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": q, "hl": "en-SG", "gl": "SG", "ceid": "SG:en"})


def safe_url(url):
    url = (url or "").strip()
    return url if re.match(r"^https?://[^\s<>\"']+$", url) else ""


def parse_closing(text, published):
    """Find a closing date like 'closes on 30 November 2026' or 'until Dec 15'."""
    m = CLOSING_RE.search(text)
    if not m:
        return None
    raw = re.sub(r"(st|nd|rd|th)\b", "", m.group(1), flags=re.I).replace(",", " ").replace(".", " ")
    raw = re.sub(r"\bsept\b", "sep", SPACE_RE.sub(" ", raw).strip(), flags=re.I)
    base = published.astimezone(MYT).date()
    for fmt in ("%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y", "%d %B", "%d %b", "%B %d", "%b %d"):
        try:
            parsed = dt.datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
        if "%Y" not in fmt:
            parsed = parsed.replace(year=base.year)
            if parsed < base:
                parsed = parsed.replace(year=base.year + 1)
        return parsed if base <= parsed <= base + dt.timedelta(days=366) else None
    return None


# ---------- fetching ----------

def fetch(feed, cutoff):
    url = google_url(feed["google"]) if feed.get("google") else feed["url"]
    try:
        parsed = feedparser.parse(url, agent=USER_AGENT)
    except Exception as exc:
        return [], f"{type(exc).__name__}: {exc}"[:120]
    if parsed.get("status", 200) >= 400:
        return [], f"HTTP {parsed['status']}"
    if not parsed.entries:
        return [], str(parsed.get("bozo_exception") or "no items")[:120]
    items = []
    for entry in parsed.entries:
        stamp = entry.get("published_parsed") or entry.get("updated_parsed")
        if not stamp:
            continue
        published = dt.datetime.fromtimestamp(calendar.timegm(stamp), tz=dt.timezone.utc)
        title, link = clean(entry.get("title")), safe_url(entry.get("link"))
        if published < cutoff or not title or not link:
            continue
        source = feed["name"]
        if entry.get("source", {}).get("title"):
            source = clean(entry.source.title)
            title = re.sub(r"\s+-\s+" + re.escape(source) + r"$", "", title)
        summary = clean(entry.get("summary"))
        if norm(summary).startswith(norm(title)[:40]) or source in summary[-80:]:
            summary = ""  # Google News summaries just repeat the headline and outlet
        items.append({"title": title, "link": link, "source": source, "published": published,
                      "summary": shorten(summary), "tags": [], "countries": [], "also": []})
    return items, None


def keywords(title):
    return {w for w in norm(title).split() if len(w) > 2 and w not in STOPWORDS}


def same_story(a, b):
    if difflib.SequenceMatcher(None, a["_key"], b["_key"]).ratio() >= SIMILARITY:
        return True
    shared = a["_words"] & b["_words"]
    return len(shared) >= 4 and len(shared) / (min(len(a["_words"]), len(b["_words"])) or 1) >= 0.5


def build(config, now):
    s = config["settings"]
    cutoff = now - dt.timedelta(days=s.get("days_back", 7))
    policy_default, cyber_default = rx(config["require_policy"]), rx(config["require_cyber"])
    tag_res = {name: rx(p) for name, p in config["tags"].items()}
    country_res = {name: rx(p) for name, p in config["countries"].items()}

    order = sorted(range(len(config["sections"])), key=lambda i: not config["sections"][i].get("first"))
    results, failures, seen, total = {}, [], [], 0
    for i in order:
        section = config["sections"][i]
        policy_re = rx(section["require_policy"]) if section.get("require_policy") else policy_default
        cyber_re = rx(section["require_cyber"]) if section.get("require_cyber") else cyber_default
        candidates = []
        for feed in section["feeds"]:
            total += 1
            items, error = fetch(feed, cutoff)
            if error:
                failures.append((section["name"], feed["name"], error))
                print(f"  ✗ {section['name']} / {feed['name']}: {error}", file=sys.stderr)
                continue
            kept = [it for it in items if policy_re.search(it["title"]) and cyber_re.search(it["title"])]
            print(f"  ✓ {section['name']} / {feed['name']}: {len(kept)} relevant of {len(items)}")
            candidates.extend(kept)

        candidates.sort(key=lambda it: it["published"], reverse=True)
        kept = []
        for it in candidates:
            text = f"{it['title']} {it['summary']}"
            it["tags"] = [name for name, r in tag_res.items() if r.search(text)]
            it["countries"] = ([section["country"]] if section.get("country")
                               else [name for name, r in country_res.items() if r.search(text)])
            it["closes"] = parse_closing(text, it["published"]) if "Consultation" in it["tags"] else None
            it["_key"], it["_words"] = norm(it["title"]), keywords(it["title"])
            match = next((o for o in seen if same_story(it, o)), None)
            if match:
                if it["source"] != match["source"] and it["source"] not in match["also"]:
                    match["also"].append(it["source"])
                continue
            seen.append(it)
            if len(kept) < s.get("max_items", 15):
                kept.append(it)
        results[i] = {"name": section["name"], "country": section.get("country"), "stories": kept}

    sections = [results[i] for i in range(len(config["sections"]))]
    return sections, failures, total


def closing_soon(config, sections, today):
    horizon = today + dt.timedelta(days=config["settings"].get("closing_soon_days", 60))
    rows = []
    for c in yaml.safe_load((DATA / "consultations.yml").read_text(encoding="utf-8")) or []:
        closes = c.get("closes")
        closes = closes if isinstance(closes, dt.date) else None
        if closes and today <= closes <= horizon and c.get("title"):
            rows.append({"title": str(c["title"]), "who": f"{c.get('regulator', '')} · {c.get('country', '')}".strip(" ·"),
                         "closes": closes, "link": safe_url(c.get("link")), "manual": True})
    for section in sections:
        for it in section["stories"]:
            if it.get("closes") and today <= it["closes"] <= horizon:
                rows.append({"title": it["title"], "who": f"{', '.join(it['countries'])} · {it['source']}".strip(" ·"),
                             "closes": it["closes"], "link": it["link"], "manual": False})
    return sorted(rows, key=lambda r: r["closes"])


# ---------- rendering ----------

CSS = """
:root{--bg:#f4f6f8;--card:#fff;--ink:#14181d;--muted:#5d6670;--line:#dfe4ea;--accent:#0b5aa6;--chip:#e8edf2;
--hot:#a8300f;--hotbg:#fde9e1;--law:#5b3cc4;--lawbg:#ece8fb;--fin:#0c6b4e;--finbg:#e0f4ec}
@media (prefers-color-scheme:dark){:root{--bg:#0e1215;--card:#161b20;--ink:#e6e9ec;--muted:#98a1ab;--line:#252c33;
--accent:#7db5f0;--chip:#20262d;--hot:#ff9b78;--hotbg:#3a1c12;--law:#b9a7ff;--lawbg:#241d3d;--fin:#6fd6ad;--finbg:#10301f}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:800px;margin:0 auto;padding:28px 16px 56px}
h1{margin:0;font-size:1.7rem;letter-spacing:-.01em}
.date{margin:2px 0 0;font-weight:600}
.meta{margin:4px 0 0;color:var(--muted);font-size:.88rem}
.filters{display:flex;flex-wrap:wrap;gap:6px;margin:14px 0 0}
.filters span{font-size:.75rem;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;align-self:center;margin-right:2px}
.filters button{border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:999px;padding:4px 11px;font:inherit;font-size:.84rem;cursor:pointer}
.filters button[aria-pressed=true]{background:var(--ink);color:var(--bg);border-color:var(--ink)}
nav{display:flex;flex-wrap:wrap;gap:4px 12px;margin:12px 0 0;font-size:.86rem}
nav a{color:var(--accent);text-decoration:none}
section{margin-top:28px}
h2{font-size:1rem;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);border-bottom:1px solid var(--line);padding-bottom:6px;margin:0 0 2px}
.soon{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--hot);border-radius:10px;padding:10px 14px;margin-top:8px}
.soon li{border:0;padding:8px 0}
.soon .d{color:var(--hot);font-weight:700;font-size:.85rem}
ol{list-style:none;margin:0;padding:0}
li{padding:13px 0;border-bottom:1px solid var(--line)}
li[hidden]{display:none}
a.t{color:var(--ink);text-decoration:none;font-weight:600}
a.t:hover{color:var(--accent);text-decoration:underline}
.s{margin:4px 0 0;color:var(--muted);font-size:.92rem}
.src{margin:6px 0 0;font-size:.8rem;color:var(--muted);display:flex;flex-wrap:wrap;gap:4px 8px;align-items:center}
.src b{color:var(--accent);font-weight:600}
.tag{font-size:.72rem;font-weight:600;padding:1px 7px;border-radius:5px;background:var(--chip);color:var(--ink)}
.tag.Consultation{background:var(--hotbg);color:var(--hot)}
.tag.New-law,.tag.Enforcement{background:var(--lawbg);color:var(--law)}
.tag.Finance{background:var(--finbg);color:var(--fin)}
.tag.c{background:transparent;border:1px solid var(--line);color:var(--muted)}
.empty{color:var(--muted);padding:10px 0}
footer{margin-top:36px;color:var(--muted);font-size:.84rem}
footer a,.archive a{color:var(--accent)}
"""

JS = """
<script>
(function(){
  var buttons=document.querySelectorAll('.filters button');
  buttons.forEach(function(btn){btn.addEventListener('click',function(){
    var tag=btn.getAttribute('data-tag');
    buttons.forEach(function(b){b.setAttribute('aria-pressed',b===btn?'true':'false')});
    document.querySelectorAll('li[data-tags]').forEach(function(li){
      li.hidden = tag!=='all' && (' '+li.getAttribute('data-tags')+' ').indexOf(' '+tag+' ')<0;
    });
  });});
})();
</script>
"""


def when(published, now):
    local, today = published.astimezone(MYT), now.astimezone(MYT).date()
    if local.date() == today:
        return local.strftime("%-I:%M %p")
    if local.date() == today - dt.timedelta(days=1):
        return "Yesterday"
    return local.strftime("%-d %b")


def page(title, body, script=""):
    return (f"<!doctype html>\n<html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<meta name='robots' content='noindex'><title>{html.escape(title)}</title>"
            f"<style>{CSS}</style></head><body><main>\n{body}\n</main>{script}</body></html>\n")


def render_html(config, sections, soon, failures, total, now):
    e = html.escape
    local = now.astimezone(MYT)
    days = config["settings"].get("days_back", 7)
    count = sum(len(s["stories"]) for s in sections)

    country_counts, tag_counts = {}, {}
    for s in sections:
        for it in s["stories"]:
            for c in it["countries"]:
                country_counts[c] = country_counts.get(c, 0) + 1
            for t in it["tags"]:
                tag_counts[t] = tag_counts.get(t, 0) + 1

    def buttons(counts, order):
        return "".join(f"<button data-tag='{slug(k)}' aria-pressed='false'>{e(k)} {counts[k]}</button>"
                       for k in order if counts.get(k))

    p = [f"<header><h1>{e(config['settings']['title'])}</h1>",
         f"<p class='date'>{local:%A, %-d %B %Y}</p>",
         f"<p class='meta'>Updated {local:%-I:%M %p} MYT · {count} policy developments in the last {days} days · "
         f"{total - len(failures)} of {total} sources responded</p></header>",
         "<div class='filters' role='group' aria-label='Filter'><button data-tag='all' aria-pressed='true'>All</button>"
         + buttons(tag_counts, list(config["tags"])) + "</div>",
         "<div class='filters' role='group' aria-label='Filter by country'><span>Country</span>"
         + buttons(country_counts, list(config["countries"])) + "</div>",
         "<nav>" + "".join(f"<a href='#{slug(s['name'])}'>{e(s['name'])}</a>" for s in sections) + "</nav>"]

    p.append("<section id='closing-soon'><h2>Consultations closing soon</h2>")
    if soon:
        rows = []
        for r in soon:
            left = (r["closes"] - local.date()).days
            label = "Closes today" if left == 0 else f"Closes {r['closes']:%a %-d %b} · {left} day{'s' if left != 1 else ''} left"
            title = (f"<a class='t' href='{e(r['link'])}' target='_blank' rel='noopener noreferrer'>{e(r['title'])}</a>"
                     if r["link"] else f"<span class='t'>{e(r['title'])}</span>")
            rows.append(f"<li><div class='d'>{e(label)}</div>{title}<p class='src'>{e(r['who'])}</p></li>")
        p.append("<div class='soon'><ol>" + "".join(rows) + "</ol></div>")
    else:
        p.append("<p class='empty'>No closing dates found this week. Add known consultations to "
                 "<code>data/consultations.yml</code>.</p>")
    p.append("</section>")

    for s in sections:
        p.append(f"<section id='{slug(s['name'])}'><h2>{e(s['name'])}</h2>")
        if not s["stories"]:
            p.append("<p class='empty'>Nothing new this week.</p></section>")
            continue
        p.append("<ol>")
        for it in s["stories"]:
            keys = [slug(t) for t in it["tags"] + it["countries"]]
            chips = "".join(f"<span class='tag {e(t.replace(' ', '-'))}'>{e(t)}</span>" for t in it["tags"])
            if not s["country"]:  # country sections don't need a country chip on every line
                chips += "".join(f"<span class='tag c'>{e(c)}</span>" for c in it["countries"])
            bits = [f"<b>{e(it['source'])}</b>", e(when(it["published"], now))]
            if it["closes"]:
                bits.append(f"closes {it['closes']:%-d %b}")
            if it["also"]:
                bits.append("also in " + e(", ".join(it["also"][:3])))
            summary = f"<p class='s'>{e(it['summary'])}</p>" if it["summary"] else ""
            p.append(f"<li data-tags='{' '.join(keys)}'><a class='t' href='{e(it['link'])}' target='_blank' "
                     f"rel='noopener noreferrer'>{e(it['title'])}</a>{summary}"
                     f"<p class='src'>{' · '.join(bits)} {chips}</p></li>")
        p.append("</ol></section>")

    p.append("<footer><p><a href='archive.html'>Past editions</a></p>")
    if failures:
        p.append("<p>Skipped this run: " + "; ".join(f"{e(a)} / {e(b)}" for a, b, _ in failures) + "</p>")
    p.append("<p>Headlines are matched automatically and may include commentary as well as official "
             "announcements. Check the regulator's own site before acting on anything here.</p></footer>")
    return page(f"{config['settings']['title']} · {local:%-d %b %Y}", "\n".join(p), JS)


def md_line(it, now):
    title = it["title"].replace("[", "(").replace("]", ")")
    tags = " ".join(f"`{t}`" for t in it["tags"])
    countries = ", ".join(it["countries"])
    detail = ", ".join(x for x in [it["source"], when(it["published"], now), countries] if x)
    return f"- [{title}]({it['link']}) ({detail}) {tags}".rstrip()


def render_markdown(config, sections, soon, failures, now):
    local = now.astimezone(MYT)
    out = [f"# {config['settings']['title']}: {local:%A, %-d %B %Y}", f"_Updated {local:%-I:%M %p} MYT_", ""]
    out += ["## Consultations closing soon", ""]
    out += [f"- **{r['closes']:%-d %b}** [{r['title']}]({r['link']}) ({r['who']})" if r["link"]
            else f"- **{r['closes']:%-d %b}** {r['title']} ({r['who']})" for r in soon] or ["_None found._"]
    out.append("")
    for s in sections:
        out += [f"## {s['name']}", ""] + ([md_line(it, now) for it in s["stories"]] or ["_Nothing new._"]) + [""]
    if failures:
        out.append("Skipped: " + "; ".join(f"{a} / {b}" for a, b, _ in failures))
    return "\n".join(out) + "\n"


def render_digest(config, sections, soon, now):
    """Body of the weekly GitHub issue. GitHub emails it to the mentioned user."""
    local = now.astimezone(MYT)
    user = config["settings"].get("github_user", "")
    site = f"https://{user}.github.io/apac-cyber-policy-tracker/" if user else ""
    count = sum(len(s["stories"]) for s in sections)
    tag_counts = {}
    for s in sections:
        for it in s["stories"]:
            for t in it["tags"]:
                tag_counts[t] = tag_counts.get(t, 0) + 1
    headline = ", ".join(f"{n} {t.lower()}" for t, n in sorted(tag_counts.items(), key=lambda x: -x[1])[:4])
    out = [f"Weekly APAC cyber policy digest for the week to {local:%-d %B %Y}.",
           f"{count} developments" + (f": {headline}." if headline else "."), ""]
    if site:
        out += [f"**Full tracker:** {site}", ""]
    out += ["## Consultations closing soon", ""]
    out += [f"1. **{r['closes']:%a %-d %b}**: [{r['title']}]({r['link']}) ({r['who']})" if r["link"]
            else f"1. **{r['closes']:%a %-d %b}**: {r['title']} ({r['who']})" for r in soon] or ["None found this week."]
    out.append("")
    for s in sections:
        if not s["stories"]:
            continue
        top = sorted(s["stories"], key=lambda it: (-sum(t in it["tags"] for t in ("New law", "Consultation", "Enforcement")),
                                                     -it["published"].timestamp()))[:5]
        out += [f"## {s['name']}", ""] + [md_line(it, now) for it in top] + [""]
    if user:
        out += ["---", f"@{user} this digest was generated automatically. Reply here to keep notes on any item."]
    return "\n".join(out) + "\n"


def write_archive(config, today):
    cutoff = today - dt.timedelta(days=config["settings"].get("keep_days", 120))
    days = []
    for path in DOCS.glob("????-??-??.html"):
        d = dt.date.fromisoformat(path.stem)
        if d < cutoff:
            path.unlink()
        else:
            days.append(d)
    items = "".join(f"<li><a href='{d.isoformat()}.html'>{d:%A, %-d %B %Y}</a></li>" for d in sorted(days, reverse=True))
    body = (f"<header><h1>{html.escape(config['settings']['title'])}</h1>"
            f"<p class='meta'>Past editions · <a href='index.html'>Latest</a></p></header>"
            f"<section class='archive'><ol>{items}</ol></section>")
    (DOCS / "archive.html").write_text(page("Past editions", body), encoding="utf-8")


def main():
    config = yaml.safe_load((ROOT / "sources.yml").read_text(encoding="utf-8"))
    now = dt.datetime.now(dt.timezone.utc)
    today = now.astimezone(MYT).date()
    print(f"Building policy tracker for {today} (MYT)")
    sections, failures, total = build(config, now)
    if len(failures) == total:
        print("Every source failed. Not overwriting the site.", file=sys.stderr)
        return 1
    soon = closing_soon(config, sections, today)

    DOCS.mkdir(exist_ok=True)
    BRIEFS.mkdir(exist_ok=True)
    (DOCS / ".nojekyll").touch()
    html_page = render_html(config, sections, soon, failures, total, now)
    (DOCS / "index.html").write_text(html_page, encoding="utf-8")
    (DOCS / f"{today.isoformat()}.html").write_text(html_page, encoding="utf-8")
    (BRIEFS / f"{today.isoformat()}.md").write_text(render_markdown(config, sections, soon, failures, now), encoding="utf-8")
    write_archive(config, today)
    if "--digest" in sys.argv:
        (ROOT / "digest.md").write_text(render_digest(config, sections, soon, now), encoding="utf-8")
        print("Wrote digest.md")
    print(f"Done: {sum(len(s['stories']) for s in sections)} items, {len(soon)} closing soon, "
          f"{len(failures)} source(s) skipped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
