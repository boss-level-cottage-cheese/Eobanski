#!/usr/bin/env python3
"""Watches listing pages for antikraak / studio spaces, filters by budget,
sends one daily phone alert via ntfy.sh (only when something is new) and writes a picture dashboard to docs/index.html.

Usage:
  python watcher.py     one run: update the list, alert only if something new appeared
"""
import datetime as dt
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import urllib.robotparser
from html.parser import HTMLParser

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(ROOT, "config.json")
STATE = os.path.join(ROOT, "state.json")
DASH = os.path.join(ROOT, "docs", "index.html")
UA = "Mozilla/5.0 (compatible; studio-watcher/1.0)"

CATEGORY_LABELS = {"antikraak": "Antikraak", "studio": "Art studio"}


# ---------- fetching and parsing ----------

class LinkParser(HTMLParser):
    def __init__(self, base):
        super().__init__()
        self.base = base
        self.links = []
        self._href = None
        self._text = []
        self._img = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a":
            self._href = a.get("href")
            self._text = []
            self._img = None
        elif tag == "img" and self._href is not None:
            if a.get("alt"):
                self._text.append(a["alt"])
            src = a.get("data-src") or a.get("src")
            if not src and a.get("srcset"):
                src = a["srcset"].split(",")[0].split(" ")[0]
            try:
                too_small = 0 < int(a.get("width") or 0) < 80
            except ValueError:
                too_small = False
            junk = re.search(r"logo|icon|sprite|avatar|placeholder|\.svg", src or "", re.I)
            if src and not src.startswith("data:") and not junk and not too_small and not self._img:
                self._img = urllib.parse.urljoin(self.base, src)

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            text = " ".join(" ".join(self._text).split())
            url = urllib.parse.urljoin(self.base, self._href).split("#")[0]
            if url.startswith("http") and text:
                self.links.append((url, text, self._img))
            self._href = None


def og_image(url):
    """Fallback: the preview picture a listing page declares for itself."""
    if not allowed(url):
        return None
    try:
        page = fetch(url)
    except Exception:
        return None
    m = (re.search(r'<meta[^>]+property=["\']og:image["\'][^>]*content=["\']([^"\']+)', page, re.I)
         or re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image', page, re.I))
    return urllib.parse.urljoin(url, html.unescape(m.group(1))) if m else None


_robots = {}


def allowed(url):
    """Respect robots.txt: skip sites that don't want automated visits."""
    p = urllib.parse.urlparse(url)
    base = "%s://%s" % (p.scheme, p.netloc)
    if base not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(base + "/robots.txt")
        try:
            rp.read()
        except Exception:
            rp = None
        _robots[base] = rp
    rp = _robots[base]
    return True if rp is None else rp.can_fetch("studio-watcher", url)


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
:root{--bg:#f6f7f9;--ink:#1b2430;--muted:#5d6b7a;--line:#d9dee5;--accent:#1f5fbf;--new:#c2410c;--star:#d99a00;--card:#fff}
@media (prefers-color-scheme:dark){:root{--bg:#12161c;--ink:#e6eaf0;--muted:#93a0af;--line:#2a323d;--accent:#7fb0ff;--new:#fb923c;--star:#f5c542;--card:#1a2029}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;padding:max(20px,env(safe-area-inset-top)) 16px 48px}
main{max-width:1100px;margin:0 auto}
h1{font-size:1.5rem;margin:0 0 4px}
.meta{color:var(--muted);font-size:.9rem;margin:0 0 16px}
.tabs{display:flex;gap:4px;border-bottom:1px solid var(--line);margin:0 0 16px}
.tabs button{font:inherit;padding:10px 16px;border:0;border-bottom:3px solid transparent;background:none;color:var(--muted);cursor:pointer}
.tabs button[aria-selected=true]{color:var(--accent);border-bottom-color:var(--accent);font-weight:600}
.pills{display:flex;gap:8px;margin:0 0 14px}
.pills button{font:inherit;font-size:.9rem;padding:6px 14px;border:1px solid var(--line);border-radius:999px;background:var(--card);color:var(--ink);cursor:pointer}
.pills button[aria-pressed=true]{border-color:var(--accent);color:var(--accent);font-weight:600}
h2{font-size:1.05rem;margin:26px 0 8px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:14px}
.card{position:relative;background:var(--card);border:1px solid var(--line);border-radius:8px;overflow:hidden;display:flex;flex-direction:column}
.card.starred{border-color:var(--star);box-shadow:0 0 0 1px var(--star)}
.pic{aspect-ratio:4/3;background:var(--line);display:block}
.pic img{width:100%;height:100%;object-fit:cover;display:block}
.body{padding:10px 12px 12px}
a{color:var(--accent);text-decoration:none;word-break:break-word}
a:hover,a:focus-visible{text-decoration:underline}
.body a{font-weight:600}
.sub{color:var(--muted);font-size:.85rem;margin-top:4px}
.badge{color:var(--new);font-weight:700;font-size:.8rem;margin-right:6px}
.star{position:absolute;top:8px;right:8px;width:38px;height:38px;border-radius:50%;border:0;background:rgba(0,0,0,.55);color:#fff;font-size:1.3rem;line-height:1;cursor:pointer}
.star[aria-pressed=true]{background:var(--star);color:#1b2430}
ul.links{list-style:none;margin:0;padding:0}
ul.links li{background:var(--card);border:1px solid var(--line);border-radius:6px;margin:0 0 8px;padding:10px 14px}
.empty{color:var(--muted)}
.err{color:var(--new);font-size:.85rem}
details{margin-top:28px}
summary{cursor:pointer;color:var(--muted)}
.hide{display:none}
</style>
</head>
<body>
<main>
<h1>Antwerp studios and antikraak</h1>
<p class="meta">Budget up to €__BUDGET__. Last checked __UPDATED__.</p>
<div class="tabs" role="tablist">
<button role="tab" id="t-list" aria-selected="true">Listings (__COUNT__)</button>
<button role="tab" id="t-check" aria-selected="false">Places to check</button>
</div>
<section id="p-list">
<div class="pills">
<button id="all" aria-pressed="true">All</button>
<button id="fav" aria-pressed="false">Starred</button>
</div>
__LISTINGS__
<p class="empty hide" id="nofav">You haven't starred anything yet.</p>
</section>
<section id="p-check" class="hide">
__CHECK__
</section>
</main>
<script>
(function(){
  var KEY="studio-stars", stars=[];
  try{stars=JSON.parse(localStorage.getItem(KEY)||"[]")}catch(e){}
  function save(){try{localStorage.setItem(KEY,JSON.stringify(stars))}catch(e){}}
  var cards=[].slice.call(document.querySelectorAll(".card"));
  var onlyFav=false;
  function paint(){
    var shown=0;
    cards.forEach(function(c){
      var on=stars.indexOf(c.dataset.url)>-1;
      c.classList.toggle("starred",on);
      c.querySelector(".star").setAttribute("aria-pressed",on);
      var hide=onlyFav&&!on;
      c.classList.toggle("hide",hide);
      if(!hide)shown++;
    });
    document.getElementById("nofav").classList.toggle("hide",!(onlyFav&&shown===0));
    document.getElementById("all").setAttribute("aria-pressed",!onlyFav);
    document.getElementById("fav").setAttribute("aria-pressed",onlyFav);
  }
  cards.forEach(function(c){
    c.querySelector(".star").addEventListener("click",function(){
      var i=stars.indexOf(c.dataset.url);
      if(i>-1)stars.splice(i,1);else stars.push(c.dataset.url);
      save();paint();
    });
  });
  document.getElementById("all").onclick=function(){onlyFav=false;paint()};
  document.getElementById("fav").onclick=function(){onlyFav=true;paint()};
  function tab(name){
    var list=name!=="check";
    document.getElementById("p-list").classList.toggle("hide",!list);
    document.getElementById("p-check").classList.toggle("hide",list);
    document.getElementById("t-list").setAttribute("aria-selected",list);
    document.getElementById("t-check").setAttribute("aria-selected",!list);
    try{history.replaceState(null,"",list?"#listings":"#check")}catch(e){}
  }
  document.getElementById("t-list").onclick=function(){tab("listings")};
  document.getElementById("t-check").onclick=function(){tab("check")};
  tab(location.hash==="#check"?"check":"listings");
  paint();
})();
</script>
</body>
</html>
"""


def card(url, item, is_new):
    price = ("€%d" % item["price"]) if item.get("price") is not None else "price not shown"
    u = html.escape(url, quote=True)
    if item.get("image"):
        pic = ('<a class="pic" href="%s"><img src="%s" alt="" loading="lazy" referrerpolicy="no-referrer" '
               'onerror="this.remove()"></a>' % (u, html.escape(item["image"], quote=True)))
    else:
        pic = '<a class="pic" href="%s" aria-hidden="true" tabindex="-1"></a>' % u
    return (
        '<div class="card" data-url="%s">%s'
        '<button class="star" type="button" aria-label="Star this listing" aria-pressed="false">&#9733;</button>'
        '<div class="body">%s<a href="%s">%s</a><div class="sub">%s, %s, %s, first seen %s</div></div></div>'
        % (
            u, pic, '<span class="badge">New</span>' if is_new else "", u, html.escape(item["title"]),
            html.escape(CATEGORY_LABELS.get(item["category"], item["category"])),
            html.escape(item["source"]), price, item["first_seen"][:10],
        )
    )


def render(cfg, state):
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=cfg.get("new_days", 3))
    gone_cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)
    active, gone = [], []
    for url, item in state["listings"].items():
        if item.get("active"):
            active.append((url, item))
        elif dt.datetime.fromisoformat(item.get("last_seen", item["first_seen"])) > gone_cutoff:
            gone.append((url, item))

    # --- Listings tab ---
    parts = []
    if active:
        parts.append('<div class="grid">')
        for url, item in sorted(active, key=lambda x: x[1]["first_seen"], reverse=True):
            parts.append(card(url, item, dt.datetime.fromisoformat(item["first_seen"]) > cutoff))
        parts.append("</div>")
    else:
        parts.append('<p class="empty">No listings found yet. Use the Places to check tab in the meantime.</p>')
    if gone:
        parts.append('<details><summary>No longer listed (last 30 days)</summary><div class="grid" style="margin-top:12px">')
        for url, item in sorted(gone, key=lambda x: x[1].get("last_seen", ""), reverse=True):
            parts.append(card(url, item, False))
        parts.append("</div></details>")
    errors = [(n, e) for n, e in state.get("errors", {}).items() if e]
    if errors:
        parts.append("<h2>Sources that could not be checked</h2>")
        for n, e in errors:
            parts.append('<p class="err">%s: %s</p>' % (html.escape(n), html.escape(e)))

    # --- Places to check tab ---
    groups = {}
    for link in cfg.get("manual_links", []):
        groups.setdefault(link.get("group", "Other"), []).append(link)
    check = []
    for group, links in groups.items():
        check.append("<h2>%s</h2><ul class=\"links\">" % html.escape(group))
        for l in links:
            note = '<div class="sub">%s</div>' % html.escape(l["note"]) if l.get("note") else ""
            check.append('<li><a href="%s">%s</a>%s</li>' % (html.escape(l["url"], quote=True), html.escape(l["name"]), note))
        check.append("</ul>")
    if not groups:
        check.append('<p class="empty">Add pages under manual_links in config.json.</p>')

    page = (
        PAGE.replace("__BUDGET__", str(cfg["budget"]))
        .replace("__UPDATED__", dt.datetime.now(dt.timezone.utc).strftime("%d %b %Y, %H:%M UTC"))
        .replace("__COUNT__", str(len(active)))
        .replace("__LISTINGS__", "\n".join(parts))
        .replace("__CHECK__", "\n".join(check))
    )
    os.makedirs(os.path.dirname(DASH), exist_ok=True)
    with open(DASH, "w", encoding="utf-8") as f:
        f.write(page)


# ---------- main ----------

def run():
    with open(CONFIG, encoding="utf-8") as f:
        cfg = json.load(f)
    state = load_state()
    state.setdefault("errors", {})
    names = {s["name"] for s in cfg["sources"]}
    # forget listings from sources that were removed from config.json
    state["listings"] = {u: i for u, i in state["listings"].items() if i["source"] in names}
    state["errors"] = {n: e for n, e in state["errors"].items() if n in names}
    new_items = []
    og_budget = 20  # max extra page fetches for pictures per run

    for src in cfg["sources"]:
        name = src["name"]
        if not allowed(src["url"]):
            state["errors"][name] = "the site does not allow automated checking (robots.txt)"
            print("Blocked by robots.txt:", name)
            continue
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
        for url, text, img in parser.links:
            ok, price = keep(src, cfg, url, text)
            if not ok:
                continue
            if url in matched:  # same listing linked twice (picture and title)
                old_text, old_price, old_img = matched[url]
                text = text if len(text) > len(old_text) else old_text
                img = img or old_img
                price = price if price is not None else old_price
            matched[url] = (text, price, img)

        baseline = not state["sources"].get(name, {}).get("initialized")
        for url, item in state["listings"].items():
            if item["source"] == name and url not in matched:
                item["active"] = False
        for url, (text, price, img) in matched.items():
            existing = state["listings"].get(url)
            if existing is None:
                existing = state["listings"][url] = {
                    "title": text[:140], "source": name, "category": src.get("category", "studio"),
                    "first_seen": now(), "price": price, "image": img, "active": True,
                }
                if not baseline:
                    new_items.append(existing)
            existing.update(active=True, price=price, last_seen=now())
            if img and not existing.get("image"):
                existing["image"] = img
            if not existing.get("image") and og_budget > 0:
                og_budget -= 1
                existing["image"] = og_image(url)
        state["sources"][name] = {"initialized": True, "last_ok": now()}

    save_state(state)
    render(cfg, state)

    # One alert per run, and none when nothing is new.
    if new_items:
        lines = "\n".join("- " + i["title"][:70] for i in new_items[:5])
        more = "\n+ %d more" % (len(new_items) - 5) if len(new_items) > 5 else ""
        push(cfg, "%d new listing%s" % (len(new_items), "" if len(new_items) == 1 else "s"),
             lines + more, cfg.get("dashboard_url") or None)

    print("Done. %d new, %d active." % (len(new_items), sum(1 for i in state["listings"].values() if i.get("active"))))


if __name__ == "__main__":
    run()
