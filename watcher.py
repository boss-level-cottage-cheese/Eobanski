#!/usr/bin/env python3
"""Watches listing pages for antikraak / studio spaces, filters by budget,
sends a phone push via ntfy.sh and writes a one-page dashboard to docs/index.html.

Usage:
  python watcher.py            normal run (alerts for new listings)
  python watcher.py --digest   normal run + a summary push of everything available
"""
import datetime as dt
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from html.parser import HTMLParser

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(ROOT, "config.json")
STATE = os.path.join(ROOT, "state.json")
DASH = os.path.join(ROOT, "docs", "index.html")
UA = "Mozilla/5.0 (compatible; studio-watcher/1.0)"

CATEGORY_LABELS = {
    "antikraak": "Antikraak",
    "studio": "Art studios",
    "social": "Check yourself (groups and boards)",
}


# ---------- fetching and parsing ----------

class LinkParser(HTMLParser):
    def __init__(self, base):
        super().__init__()
        self.base = base
        self.links = []
        self._href = None
        self._text = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            text = " ".join("".join(self._text).split())
            url = urllib.parse.urljoin(self.base, self._href).split("#")[0]
            if url.startswith("http") and text:
                self.links.append((url, text))
            self._href = None


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "nl,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def to_number(s):
    s = s.strip().rstrip(".,-")
    if re.fullmatch(r"\d{1,3}(\.\d{3})+(,\d+)?", s):
        s = s.replace(".", "").replace(",", ".")
    elif "," in s and "." not in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def prices(text):
    found = re.findall(r"€\s?(\d[\d.,]*)", text) + re.findall(r"(\d[\d.,]*)\s?(?:€|euro\b|eur\b)", text, re.I)
    nums = [to_number(f) for f in found]
    return [n for n in nums if n is not None]


def keep(src, cfg, url, text):
    """Return (keep, price_or_None)."""
    pattern = src.get("include_pattern")
    if pattern and not re.search(pattern, url):
        return False, None
    if src.get("require_keywords", True):
        low = (text + " " + url).lower()
        if not any(k.lower() in low for k in cfg["keywords"]):
            return False, None
    p = prices(text)
    if p and min(p) > cfg["budget"]:
        return False, None
    return True, (min(p) if p else None)


# ---------- state ----------

def load_state():
    if os.path.exists(STATE):
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    return {"sources": {}, "listings": {}}


def save_state(state):
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1, ensure_ascii=False)


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes")


# ---------- notifications ----------

def push(cfg, title, message, click=None):
    topic = os.environ.get("NTFY_TOPIC") or cfg.get("ntfy_topic")
    if not topic:
        print("No ntfy topic set; skipping push:", title)
        return
    headers = {"Title": title, "Tags": "house"}
    if click:
        headers["Click"] = click
    req = urllib.request.Request(
        "https://ntfy.sh/" + urllib.parse.quote(topic),
        data=message.encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=20).read()
    except Exception as e:  # a failed push must not break the run
        print("Push failed:", e)


# ---------- dashboard ----------

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Antwerp studios and antikraak</title>
<style>
:root{--bg:#f7f8fa;--ink:#1b2430;--muted:#5d6b7a;--line:#d9dee5;--accent:#1f5fbf;--new:#c2410c;--card:#fff}
@media (prefers-color-scheme:dark){:root{--bg:#12161c;--ink:#e6eaf0;--muted:#93a0af;--line:#2a323d;--accent:#7fb0ff;--new:#fb923c;--card:#1a2029}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;padding:max(20px,env(safe-area-inset-top)) 16px 40px}
main{max-width:720px;margin:0 auto}
h1{font-size:1.5rem;margin:0 0 4px}
.meta{color:var(--muted);font-size:.9rem;margin:0 0 28px}
h2{font-size:1.1rem;margin:32px 0 8px;padding-bottom:6px;border-bottom:1px solid var(--line)}
ul{list-style:none;margin:0;padding:0}
li{background:var(--card);border:1px solid var(--line);border-radius:6px;margin:0 0 8px;padding:12px 14px}
a{color:var(--accent);text-decoration:none;word-break:break-word}
a:hover,a:focus-visible{text-decoration:underline}
.sub{color:var(--muted);font-size:.85rem;margin-top:2px}
.badge{color:var(--new);font-weight:600;font-size:.8rem;margin-right:6px}
.empty{color:var(--muted);font-style:italic}
.err{color:var(--new);font-size:.85rem}
</style>
</head>
<body>
<main>
<h1>Antwerp studios and antikraak</h1>
<p class="meta">Budget up to €__BUDGET__. Last checked __UPDATED__.</p>
__BODY__
</main>
</body>
</html>
"""


def render(cfg, state):
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=cfg.get("new_days", 3))
    by_cat = {c: [] for c in CATEGORY_LABELS}
    for url, item in state["listings"].items():
        if item.get("active"):
            by_cat.setdefault(item["category"], []).append((url, item))

    parts = []
    for cat, label in CATEGORY_LABELS.items():
        parts.append("<h2>%s</h2>" % html.escape(label))
        count = 0
        for url, item in sorted(by_cat.get(cat, []), key=lambda x: x[1]["first_seen"], reverse=True):
            count += 1
            is_new = dt.datetime.fromisoformat(item["first_seen"]) > cutoff
            price = ("€%d" % item["price"]) if item.get("price") is not None else "price not shown"
            parts.append(
                '<li>%s<a href="%s">%s</a><div class="sub">%s, %s, first seen %s</div></li>'
                % (
                    '<span class="badge">New</span>' if is_new else "",
                    html.escape(url, quote=True),
                    html.escape(item["title"]),
                    html.escape(item["source"]),
                    price,
                    item["first_seen"][:10],
                )
            )
        for link in cfg.get("manual_links", []):
            if link.get("category", "social") == cat:
                count += 1
                parts.append(
                    '<li><a href="%s">%s</a><div class="sub">Open and check by hand</div></li>'
                    % (html.escape(link["url"], quote=True), html.escape(link["name"]))
                )
        if count == 0:
            parts.append('<p class="empty">Nothing matching your budget right now.</p>')

    errors = [(n, e) for n, e in state.get("errors", {}).items() if e]
    if errors:
        parts.append("<h2>Sources that failed on the last run</h2>")
        for n, e in errors:
            parts.append('<p class="err">%s: %s</p>' % (html.escape(n), html.escape(e)))

    page = (
        PAGE.replace("__BUDGET__", str(cfg["budget"]))
        .replace("__UPDATED__", dt.datetime.now(dt.timezone.utc).strftime("%d %b %Y, %H:%M UTC"))
        .replace("__BODY__", "\n".join(parts))
    )
    os.makedirs(os.path.dirname(DASH), exist_ok=True)
    with open(DASH, "w", encoding="utf-8") as f:
        f.write(page)


# ---------- main ----------

def run(digest=False):
    with open(CONFIG, encoding="utf-8") as f:
        cfg = json.load(f)
    state = load_state()
    state.setdefault("errors", {})
    new_items = []

    for src in cfg["sources"]:
        name = src["name"]
        try:
            page = fetch(src["url"])
        except Exception as e:
            state["errors"][name] = str(e)[:200]
            print("Failed:", name, e)
            continue
        state["errors"][name] = ""

        parser = LinkParser(src["url"])
        parser.feed(page)
        matched = {}
        for url, text in parser.links:
            ok, price = keep(src, cfg, url, text)
            if ok:
                matched[url] = (text, price)

        baseline = not state["sources"].get(name, {}).get("initialized")
        for url, item in state["listings"].items():
            if item["source"] == name and url not in matched:
                item["active"] = False
        for url, (text, price) in matched.items():
            if url not in state["listings"]:
                state["listings"][url] = {
                    "title": text[:140], "source": name, "category": src.get("category", "studio"),
                    "first_seen": now(), "price": price, "active": True,
                }
                if not baseline:
                    new_items.append(state["listings"][url] | {"url": url})
            else:
                state["listings"][url].update(active=True, price=price)
        state["sources"][name] = {"initialized": True, "last_ok": now()}

    save_state(state)
    render(cfg, state)

    dash = cfg.get("dashboard_url") or None
    if len(new_items) > 5:
        push(cfg, "%d new listings" % len(new_items), "Open the dashboard to see them.", dash)
    else:
        for it in new_items:
            price = ("€%d" % it["price"]) if it["price"] is not None else "price not shown"
            push(cfg, "New: " + it["category"], "%s (%s)\n%s" % (it["title"], price, it["source"]), it["url"])

    if digest:
        active = [(u, i) for u, i in state["listings"].items() if i.get("active")]
        top = "\n".join("- " + i["title"][:60] for _, i in active[:5]) or "Nothing matching right now."
        push(cfg, "Weekly: %d available" % len(active), top, dash)

    print("Done. %d new, %d active." % (len(new_items), sum(1 for i in state["listings"].values() if i.get("active"))))


if __name__ == "__main__":
    run(digest="--digest" in sys.argv)
