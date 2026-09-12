import base64
import datetime
import hashlib
import html
import json
import os
import re
import time
import urllib.parse
import warnings
import xml.etree.ElementTree as ET
import zoneinfo
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher

warnings.filterwarnings("ignore", category=UserWarning, module="google.genai")

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
import feedparser
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

# --- Konfiguration ---
DEFAULT_PRIO = 1
MAX_RETENTION_HOURS = 48
MAX_DEDUP_HOURS = 20
REMOTE_DATA_URL = "https://schoerb.github.io/news-hub/data.json"
BERLIN_TZ = zoneinfo.ZoneInfo("Europe/Berlin")
DEDUP_RATIO = float(os.environ.get("DEDUP_RATIO", "0.78"))
DEDUP_OVERLAP = float(os.environ.get("DEDUP_OVERLAP", "0.65"))

STOPWORDS = {
    "im", "in", "der", "die", "das", "den", "dem", "des", "für", "von", "mit", "ab", "sofort",
    "neu", "neue", "neues", "neuen", "neuer", "update", "bringt", "startet", "erhält", "offiziell",
    "jetzt", "nach", "zum", "zur", "wie", "auf", "ein", "eine", "einen", "einem", "einer",
    "als", "sich", "nicht", "auch", "über", "test", "bericht", "schlägt", "zeigt", "soll", "gibt",
    "the", "a", "an", "and", "or", "to", "for", "of", "with", "by", "from", "is", "are", "new", "out"
}

# --- Connection Pool ---
session = requests.Session()
adapter = HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=Retry(total=2, backoff_factor=0.3))
session.mount("https://", adapter)
session.mount("http://", adapter)
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36",
    "Accept": "application/rss+xml, application/xml, text/xml, */*;q=0.8",
})

# --- Krypto-Helfer ---
def openssl_kdf(password: bytes, salt: bytes, key_len=32, iv_len=16) -> tuple[bytes, bytes]:
    d = b""
    while len(d) < (key_len + iv_len):
        d += hashlib.md5(d[-16:] + password + salt if d else password + salt).digest()
    return d[:key_len], d[key_len:key_len + iv_len]


def encrypt_payload(data: str, pw: str) -> str:
    if not pw:
        return data
    salt = os.urandom(8)
    key, iv = openssl_kdf(pw.encode(), salt)
    pad = 16 - (len(data.encode()) % 16)
    c = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return base64.b64encode(b"Salted__" + salt + c.update(data.encode() + bytes([pad] * pad)) + c.finalize()).decode()


def decrypt_payload(enc: str, pw: str) -> str:
    if not pw:
        return enc
    try:
        raw = base64.b64decode(enc)
        if not raw.startswith(b"Salted__"):
            return enc
        key, iv = openssl_kdf(pw.encode(), raw[8:16])
        c = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
        d = c.update(raw[16:]) + c.finalize()
        return d[:-d[-1]].decode("utf-8")
    except Exception:
        return ""


def hash_feed(url: str) -> str:
    return hashlib.sha256((url + os.environ.get("PAGE_PASSWORD", "static_news_salt")).encode()).hexdigest()[:16]


# --- Pydantic Schemas ---
class DeltaItem(BaseModel):
    id: int
    german_title: str = Field(description="Zwingend auf DEUTSCH übersetzen. Kein Clickbait.")
    summary: str = Field(description="Genau 1 deutscher Satz mit **fett** hervorgehobenen Begriffen.")
    use_image: bool = Field(default=False)


class DeltaBatchResponse(BaseModel):
    items: list[DeltaItem]


def clean_url(url: str) -> str:
    if not url:
        return ""
    p = urllib.parse.urlsplit(url)
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True)
         if not (k.startswith("utm_") or k in ("wt_mc", "fbclid", "ref", "source"))]
    return urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, urllib.parse.urlencode(q), p.fragment))


def parse_opml():
    raw = os.environ.get("FEEDS_OPML", "").strip()
    prios = json.loads(os.environ.get("FEED_PRIORITIES", "{}"))
    try:
        tree = ET.fromstring(raw) if raw else (ET.parse("feeds.opml").getroot() if os.path.exists("feeds.opml") else None)
    except Exception:
        return []
    if tree is None:
        return []
    feeds = []
    for n in tree.findall(".//outline[@xmlUrl]"):
        u = n.get("xmlUrl", "").strip()
        if u:
            name = (n.get("text") or n.get("title") or "Feed").strip()
            prio = int(n.get("priority")) if n.get("priority", "").isdigit() else prios.get(name, DEFAULT_PRIO)
            feeds.append({"title": name, "url": u, "priority": prio})
    return feeds


def extract_image(e):
    for k in ("media_content", "media_thumbnail"):
        if e.get(k):
            u = e[k][0].get("url")
            if u and not any(b in u.lower() for b in ["favicon", "avatar", "logo", "tracking", "1x1"]):
                return u
    for enc in e.get("enclosures", []):
        if enc.get("type", "").startswith("image/") and enc.get("href"):
            return enc.get("href")
    c = e.get("summary", "") + (e.content[0].get("value", "") if "content" in e and e.content else "")
    m = re.search(r'<img[^>]+src=["\']?([^\s"\'<>]+\.(?:jpg|jpeg|png|webp))', c, re.I)
    return m.group(1) if m and not any(b in m.group(1).lower() for b in ["favicon", "pixel", "1x1"]) else None


def parse_timestamp(iso_val) -> int:
    if isinstance(iso_val, (int, float)):
        return int(iso_val)
    if not iso_val:
        return 0
    try:
        return int(datetime.datetime.fromisoformat(str(iso_val).replace("Z", "+00:00")).timestamp())
    except Exception:
        return 0


def load_cached_state():
    arts, meta = [], {}
    pw = os.environ.get("PAGE_PASSWORD", "")
    force = os.environ.get("FORCE_REFRESH", "").lower() in ("true", "1")
    raw = ""

    if os.path.exists("public/data.json"):
        try:
            with open("public/data.json", "r", encoding="utf-8") as f:
                raw = f.read().strip()
        except Exception:
            pass
    elif REMOTE_DATA_URL and not force:
        try:
            r = session.get(REMOTE_DATA_URL, timeout=(3.05, 5.0))
            if r.ok:
                raw = r.text.strip()
        except Exception:
            pass

    if raw and raw != "[]":
        try:
            arts = json.loads(decrypt_payload(raw, pw) if pw else raw)
        except Exception:
            pass

    if os.path.exists("cache_meta.json") and not force:
        try:
            with open("cache_meta.json", "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            pass

    for a in arts:
        a["_ts"] = parse_timestamp(a.get("published"))
    return arts, meta


def fetch_all_feeds(feeds, cache_meta):
    now = datetime.datetime.now(datetime.timezone.utc)
    cutoff = int((now - datetime.timedelta(hours=MAX_RETENTION_HOURS)).timestamp())
    new_meta, health = dict(cache_meta), []

    def _fetch(f):
        url, key = f["url"], hash_feed(f["url"])
        headers = {}
        if key in cache_meta:
            if "etag" in cache_meta[key]:
                headers["If-None-Match"] = cache_meta[key]["etag"]
            if "modified" in cache_meta[key]:
                headers["If-Modified-Since"] = cache_meta[key]["modified"]

        try:
            p = urllib.parse.urlsplit(url)
            if p.scheme and p.netloc:
                headers["Referer"] = f"{p.scheme}://{p.netloc}/"

            r = session.get(url, headers=headers, timeout=(3.05, 6.0))
            if r.status_code == 304:
                return [], {"title": f["title"], "status": "ok", "code": 304}
            if not r.ok:
                return [], {"title": f["title"], "status": "error", "code": r.status_code}

            m = {}
            if "etag" in r.headers:
                m["etag"] = r.headers["etag"]
            if "last-modified" in r.headers:
                m["modified"] = r.headers["last-modified"]
            if m:
                new_meta[key] = m

            parsed = feedparser.parse(r.content)
            if parsed.bozo and not parsed.entries:
                return [], {"title": f["title"], "status": "parse_error", "code": r.status_code}

            items = []
            for e in parsed.entries[:15]:
                dt = None
                for attr in ("published_parsed", "updated_parsed", "created_parsed"):
                    if getattr(e, attr, None):
                        dt = datetime.datetime(*getattr(e, attr)[:6], tzinfo=datetime.timezone.utc)
                        break
                ts = int(dt.timestamp()) if dt else int(now.timestamp())
                if ts > cutoff:
                    summary = " ".join(re.sub(r"<[^>]+>", " ", e.get("summary", "")).split())[:350]
                    items.append({
                        "title": e.title.strip(),
                        "summary": summary,
                        "link": clean_url(e.link.strip()),
                        "image": extract_image(e),
                        "source": f["title"].strip(),
                        "priority": f["priority"],
                        "published": dt.isoformat() if dt else now.isoformat(),
                        "_ts": ts,
                    })
            return items, {"title": f["title"], "status": "ok", "code": r.status_code}
        except Exception:
            return [], {"title": f["title"], "status": "exception", "code": "timeout"}

    all_items = []
    with ThreadPoolExecutor(max_workers=12) as ex:
        for items, h in ex.map(_fetch, feeds):
            all_items.extend(items)
            health.append(h)
    return all_items, new_meta, health


# --- Deduplizierung ---
def clean_stem(w: str) -> str:
    for end in ("s", "n", "en", "er", "es", "e"):
        if w.endswith(end) and len(w) > 4:
            return w[:-len(end)]
    return w


def extract_features(title: str):
    words = re.sub(r"[^\w\s\.]", " ", title.lower()).split()
    nums = {w for w in words if any(c.isdigit() for c in w) and len(w) >= 2}
    kws = {clean_stem(w) for w in words if w not in STOPWORDS and len(w) > 2 and w not in nums}
    return kws, nums


def is_dup(t_a: str, t_b: str, feat_a, feat_b) -> bool:
    kw_a, num_a = feat_a
    kw_b, num_b = feat_b
    if (num_a & num_b) and len(kw_a & kw_b) >= 2:
        return True
    sub = {wa[:5] for wa in kw_a for wb in kw_b if len(wa) >= 5 and len(wb) >= 5 and wa[:5] == wb[:5]}
    min_len = min(len(kw_a), len(kw_b))
    if min_len >= 3 and (len(kw_a & kw_b | sub) / min_len) >= DEDUP_OVERLAP:
        return True
    if (kw_a & kw_b or sub) and min(len(t_a), len(t_b)) / max(len(t_a), len(t_b)) >= 0.65:
        return SequenceMatcher(None, re.sub(r"[^\w\s]", "", t_a.lower()), re.sub(r"[^\w\s]", "", t_b.lower())).ratio() >= DEDUP_RATIO
    return False


def consolidate_articles(articles: list[dict]) -> list[dict]:
    sorted_arts = sorted(articles, key=lambda x: x.get("priority", DEFAULT_PRIO), reverse=True)
    res, feats = [], []
    max_diff = MAX_DEDUP_HOURS * 3600

    for item in sorted_arts:
        feat = extract_features(item["title"])
        ts = item.get("_ts", 0)
        match = next((ex for i, ex in enumerate(res)
                      if not (ts and ex.get("_ts") and abs(ts - ex["_ts"]) > max_diff) and is_dup(item["title"], ex["title"], feat, feats[i])), None)
        if match:
            src = item.get("source")
            others = match.setdefault("other_sources", [])
            if src and src != match.get("source") and src not in others:
                others.append(src)
            for osrc in item.get("other_sources", []):
                if osrc != match.get("source") and osrc not in others:
                    others.append(osrc)
            match.setdefault("merged_details", []).append({
                "source": src or "Unbekannt",
                "title": item.get("title", ""),
                "link": item.get("link", ""),
                "matched_with": match.get("title", "")
            })
            match["merged_details"].extend(item.get("merged_details", []))
        else:
            c = dict(item)
            c.setdefault("other_sources", [])
            c.setdefault("merged_details", [])
            res.append(c)
            feats.append(feat)
    return res


# --- Gemini API ---
def summarize_chunk_with_gemini(client, chunk, max_retries=3):
    payload = [{"id": i, "original_title": a["title"], "source": a["source"], "raw_text": a["summary"], "has_image": bool(a.get("image"))} for i, a in enumerate(chunk)]
    prompt = f"Chefredakteur Tech-News:\n1. 'german_title': Übersetze englische Titel vollständig ins Deutsche (Kein Clickbait).\n2. 'summary': Genau 1 deutscher Satz. Schlüsselwörter mit **fett** markieren.\n3. 'use_image': True nur bei echten Geräten/Screenshots/Charts.\nArtikel:\n{json.dumps(payload, ensure_ascii=False)}"

    for attempt in range(max_retries):
        model = "gemini-3.5-flash-lite" if attempt == 0 else "gemini-3.6-flash"
        try:
            res = client.models.generate_content(
                model=model, contents=prompt,
                config=types.GenerateContentConfig(temperature=0.1, response_mime_type="application/json", response_schema=DeltaBatchResponse)
            )
            parsed = DeltaBatchResponse.model_validate_json(res.text)
            out = []
            for it in parsed.items:
                if 0 <= it.id < len(chunk):
                    orig = chunk[it.id]
                    clean_s = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", html.escape((it.summary or "").strip())) or html.escape(orig.get("summary", ""))[:180]
                    out.append({
                        "title": html.escape((it.german_title or "").strip()) or orig["title"],
                        "link": orig["link"], "source": orig["source"], "other_sources": orig.get("other_sources", []),
                        "merged_details": orig.get("merged_details", []), "summary": clean_s,
                        "image": orig["image"] if it.use_image else None, "published": orig["published"], "_ts": orig.get("_ts", 0),
                    })
            return out
        except Exception as err:
            time.sleep(22 + (attempt * 6) if ("429" in str(err) or "RESOURCE_EXHAUSTED" in str(err)) else 2 ** (attempt + 1))

    return [{"title": o["title"], "link": o["link"], "source": o["source"], "other_sources": o.get("other_sources", []),
             "merged_details": o.get("merged_details", []), "summary": html.escape(o["summary"]), "image": o["image"],
             "published": o["published"], "_ts": o.get("_ts", 0)} for o in chunk]


def summarize_delta_with_gemini(items):
    key = os.environ.get("GEMINI_API_KEY")
    if not items or not key:
        return []
    client = genai.Client(api_key=key)
    chunks = [items[i:i + 35] for i in range(0, len(items), 35)]
    res = []
    with ThreadPoolExecutor(max_workers=2) as ex:
        for r in ex.map(lambda c: summarize_chunk_with_gemini(client, c), chunks):
            res.extend(r)
    return res


# --- Unified Template Engine ---
PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="de" data-theme="dark">
<head>
  <meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
  <title>__PAGE_TITLE__</title>
  <meta name="mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="theme-color" content="#121418">
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap">
  <script src="https://cdnjs.cloudflare.com/ajax/libs/crypto-js/4.2.0/crypto-js.min.js"></script>
  <style>
    :root{--bg:#121418;--sidebar:#181b20;--card:#1e2229;--hover:#262b34;--border:#2e3440;--text:#e2e8f0;--muted:#94a3b8;--bold:#f1f5f9;--accent:#2ecc71;--accent-dim:rgba(46,204,113,.15);--link:#60a5fa}
    [data-theme="light"]{--bg:#f8fafc;--sidebar:#fff;--card:#fff;--hover:#f1f5f9;--border:#e2e8f0;--text:#1e293b;--muted:#64748b;--bold:#0f172a;--accent:#16a34a;--accent-dim:rgba(22,163,74,.12);--link:#2563eb}
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:'Inter',system-ui,sans-serif;background:var(--bg);color:var(--text);display:flex;height:100vh;overflow:hidden}
    .modal-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.75);backdrop-filter:blur(2px);z-index:1100;align-items:center;justify-content:center;padding:16px}
    .modal-card{background:var(--sidebar);border:1px solid var(--border);border-radius:12px;padding:24px;width:100%;max-width:540px;max-height:80vh;display:flex;flex-direction:column;box-shadow:0 16px 36px rgba(0,0,0,.3)}
    .modal-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;border-bottom:1px solid var(--border);padding-bottom:10px}
    .modal-body{overflow-y:auto;flex-grow:1;font-size:.88rem}
    .modal-row{display:flex;justify-content:space-between;align-items:center;padding:10px 0;border-bottom:1px solid var(--border);gap:12px}
    .btn{background:var(--card);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:1.05rem;padding:6px 10px;cursor:pointer}
    .btn.active{background:var(--accent-dim);border-color:var(--accent);color:var(--accent)}
    .btn-action{width:100%;background:var(--accent);color:#fff;border:none;padding:12px;border-radius:6px;font-weight:600;cursor:pointer;margin-top:14px}
    .sidebar-backdrop{display:none;position:fixed;inset:0;background:rgba(0,0,0,.65);z-index:110}
    .sidebar{width:290px;background:var(--sidebar);border-right:1px solid var(--border);display:flex;flex-direction:column;flex-shrink:0;z-index:120;transition:all .25s ease;overflow:hidden;white-space:nowrap}
    .sidebar.collapsed{width:0!important;border:none!important;visibility:hidden}
    .sidebar-header{padding:16px 20px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center}
    .source-list{list-style:none;padding:12px;overflow-y:auto;flex-grow:1}
    .source-btn{width:100%;text-align:left;padding:10px 14px;margin-bottom:4px;border-radius:6px;background:transparent;border:none;color:var(--muted);font-size:.85rem;font-weight:500;cursor:pointer;display:flex;justify-content:space-between;align-items:center}
    .source-btn:hover,.source-btn.active{background:var(--accent-dim);color:var(--accent);font-weight:600}
    .badge{background:var(--border);padding:2px 8px;border-radius:12px;font-size:.7rem;color:var(--text)}
    .source-btn.active .badge{background:var(--accent);color:#fff}
    .sidebar-footer{padding:16px;border-top:1px solid var(--border)}
    .nav-link{display:block;text-align:center;color:var(--accent);text-decoration:none;font-size:.82rem;font-weight:600;padding:8px;border-radius:6px;background:var(--accent-dim);margin-bottom:8px}
    .main{flex-grow:1;overflow-y:auto;position:relative}
    
    .stream-header{position:sticky;top:0;z-index:50;background:rgba(18,20,24,.55);backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);border-bottom:1px solid var(--border);padding:10px 16px;display:flex;justify-content:space-between;align-items:center;gap:12px;transition:transform .28s ease}
    .stream-header.header-hidden{transform:translateY(-100%)}
    [data-theme="light"] .stream-header{background:rgba(248,250,252,.65)}
    
    .header-left{display:flex;align-items:center;gap:12px;flex-shrink:0;min-width:0}
    .header-title-group{display:flex;flex-direction:column;gap:2px}
    .stream-header h2{font-size:1.15rem;font-weight:700;color:var(--bold);white-space:nowrap}
    .header-meta{display:flex;align-items:center;gap:6px;font-size:.8rem;color:var(--muted);white-space:nowrap}
    .header-right{display:flex;align-items:center;gap:8px;flex-grow:1;justify-content:flex-end;max-width:560px}
    .search-input{background:var(--card);border:1px solid var(--border);color:var(--text);padding:8px 14px;border-radius:6px;font-size:.85rem;outline:none;width:100%;max-width:300px}
    
    .cards-grid{padding:14px 16px calc(24px + env(safe-area-inset-bottom,0px));display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:12px}
    .feed-card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:16px;display:flex;flex-direction:column;justify-content:space-between;transition:transform .15s}
    .feed-card:hover{transform:translateY(-2px);background:var(--hover)}
    
    .unread-dot-btn{background:none;border:none;padding:0;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;width:18px;height:18px}
    .unread-dot{width:8px;height:8px;border-radius:50%;background:var(--accent);box-shadow:0 0 8px var(--accent);transition:all .2s}
    .feed-card.seen .unread-dot{background:var(--muted);box-shadow:none;opacity:.55}
    .feed-card.read .unread-dot{background:transparent;box-shadow:none;border:1.5px solid var(--muted);opacity:.35}
    .feed-card.read .feed-title{color:var(--muted)}
    
    .bookmark-btn{background:none;border:none;padding:0;cursor:pointer;font-size:.9rem;opacity:.4;transition:all .15s}
    .bookmark-btn:hover{opacity:.8;transform:scale(1.15)}
    .feed-card.bookmarked .bookmark-btn{opacity:1;filter:drop-shadow(0 0 4px rgba(234,179,8,.6))}
    
    .feed-meta{display:flex;align-items:center;gap:6px;font-size:.75rem;margin-bottom:8px;flex-wrap:wrap}
    .feed-source{color:var(--accent);font-weight:600}
    .feed-time,.feed-others{color:var(--muted);font-size:.72rem}
    .feed-title{font-size:1.02rem;font-weight:600;color:var(--bold);text-decoration:none;line-height:1.4;margin-bottom:8px}
    .feed-title:hover{color:var(--link);text-decoration:underline}
    .feed-summary{font-size:.86rem;color:var(--muted);line-height:1.45;margin-bottom:12px}
    .feed-summary strong{color:var(--bold);font-weight:600}
    .feed-thumb{width:100%;height:150px;object-fit:cover;border-radius:6px;margin-top:auto}
    .meta-clickable{color:var(--accent);cursor:pointer}
    .meta-clickable:hover{text-decoration:underline}

    .toast-container{position:fixed;bottom:20px;right:20px;z-index:1000;display:none;max-width:380px;background:var(--sidebar);border:1px solid var(--border);border-radius:10px;padding:12px 16px;box-shadow:0 8px 24px rgba(0,0,0,.45);font-size:.85rem;color:var(--text);align-items:center;gap:12px;animation:slideUp .25s ease}
    @keyframes slideUp{from{transform:translateY(20px);opacity:0}to{transform:translateY(0);opacity:1}}
    .toast-container a{color:var(--link);text-decoration:none;font-weight:600}
    .toast-close{background:none;border:none;color:var(--muted);font-size:1.2rem;cursor:pointer;padding:0 4px}

    @media (max-width:768px){
      .sidebar{position:fixed;inset:0 auto 0 0;transform:translateX(-100%);box-shadow:4px 0 24px rgba(0,0,0,.6)}
      .sidebar.open{transform:translateX(0);visibility:visible!important;width:290px!important}
      .sidebar-backdrop.open{display:block}
      .stream-header{padding:8px 10px;flex-direction:column;align-items:stretch;gap:8px}
      .header-left{width:100%}
      .stream-header h2{font-size:1rem;white-space:normal}
      .header-meta{font-size:.75rem;flex-wrap:wrap}
      .header-right{width:100%;max-width:100%;display:flex;gap:6px}
      .search-input{max-width:100%}
      .cards-grid{grid-template-columns:1fr;gap:8px;padding:8px 8px calc(24px + env(safe-area-inset-bottom,0px))}
      .feed-card{padding:14px}
      .toast-container{left:12px;right:12px;bottom:calc(16px + env(safe-area-inset-bottom,0px));max-width:none}
    }
  </style>
</head>
<body>
  <div id="auth-overlay" class="modal-overlay" style="z-index:2000">
    <div class="modal-card" style="max-width:380px;text-align:center">
      <h2 style="margin-bottom:8px">🔐 Geschützt</h2>
      <input type="password" id="auth-pwd" class="search-input" style="max-width:100%;margin-bottom:12px" placeholder="Passwort..." onkeydown="if(event.key==='Enter')submitAuth()">
      <button class="btn-action" style="margin-top:0" onclick="submitAuth()">Entschlüsseln</button>
      <div id="auth-err" style="color:#ef4444;font-size:.8rem;margin-top:8px;display:none">Ungültiges Passwort!</div>
    </div>
  </div>

  <div id="health-modal" class="modal-overlay">
    <div class="modal-card">
      <div class="modal-header"><h2>📡 Feed-Status</h2><button class="btn" onclick="toggleModal('health-modal',false)">&times;</button></div>
      <div class="modal-body">
        <div style="margin-bottom:12px;padding:10px;background:var(--card);border:1px solid var(--border);border-radius:6px">
          <a href="https://github.com/schoerb/news-hub/actions" target="_blank" rel="noopener" style="color:var(--link);text-decoration:none;display:flex;justify-content:space-between"><span>⚙️ Workflow-Status</span><span>↗</span></a>
        </div>
        <div id="health-list"></div>
      </div>
      <button class="btn-action" onclick="toggleModal('health-modal',false)">Schließen</button>
    </div>
  </div>

  <div id="dup-modal" class="modal-overlay">
    <div class="modal-card">
      <div class="modal-header"><h2>🧹 Bereinigte Duplikate</h2><button class="btn" onclick="toggleModal('dup-modal',false)">&times;</button></div>
      <div id="dup-list" class="modal-body"></div>
      <button class="btn-action" onclick="toggleModal('dup-modal',false)">Schließen</button>
    </div>
  </div>

  <div id="toast" class="toast-container">
    <span id="toast-msg" style="flex:1"></span>
    <button class="toast-close" onclick="hideToast()">&times;</button>
  </div>

  <div class="sidebar-backdrop" id="backdrop" onclick="toggleSidebar()"></div>
  <aside class="sidebar" id="sidebar">
    <div class="sidebar-header"><h1>⚡ __SIDEBAR_TITLE__</h1><button class="btn" onclick="toggleSidebar()">&times;</button></div>
    <ul class="source-list" id="source-list"></ul>
    <div class="sidebar-footer">
      <a href="__NAV_TARGET_URL__" class="nav-link">__NAV_TARGET_TEXT__</a>
      __MARK_ALL_BTN__
    </div>
  </aside>

  <main class="main">
    <div class="stream-header">
      <div class="header-left">
        <button class="btn" onclick="toggleSidebar()">☰</button>
        <div class="header-title-group">
          <h2 id="current-title">Meldungen laden...</h2>
          <div class="header-meta">
            <span class="meta-clickable" id="header-dup-info" onclick="openDupModal()">🧹 Duplikate</span>
            <span style="color:var(--border)">•</span>
            __HEALTH_BLOCK__
          </div>
        </div>
      </div>
      <div class="header-right">
        <input type="search" class="search-input" id="search-box" placeholder="Durchsuchen..." oninput="filterSearch(this.value)">
        <div style="display:flex;gap:6px">
          <button class="btn" id="saved-filter-btn" onclick="toggleSavedFilter()" title="Später lesen (Lesezeichen)">🔖 <span id="saved-count">0</span></button>
          __DESKTOP_REFRESH_BTN__
          <button class="btn" onclick="toggleTheme()">🌓</button>
        </div>
      </div>
    </div>
    <div id="articles-container" class="cards-grid"></div>
  </main>

  <script>
    window.IS_ARCHIVE = __IS_ARCHIVE__;
    const configuredSources = __CONFIGURED_SOURCES__, feedHealth = __HEALTH_DATA__, buildTime = "__NOW_STR__";
    let rawData = "", globalArticles = [], liveArticles = [], counts = {}, activeSource = 'all', searchQuery = '', onlySaved = false, sIndex = -1, sTimer = null, toastTimer = null;

    const esc = s => (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    const hStr = s => { let h = 0; for(let i=0;i<(s||'').length;i++){ h = ((h<<5)-h)+s.charCodeAt(i); h |= 0; } return Math.abs(h); };
    const relTime = d => {
      if(!d) return ''; const diff = Math.floor((Date.now() - new Date(d))/1000);
      if(isNaN(diff)) return ''; if(diff < 60) return '• gerade'; if(diff < 3600) return `• vor ${Math.floor(diff/60)}m`;
      if(diff < 86400) return `• vor ${Math.floor(diff/3600)}h`; return `• vor ${Math.floor(diff/86400)}d`;
    };

    function initTheme(){ document.documentElement.setAttribute('data-theme', localStorage.getItem('hub_theme') || (matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark')); }
    function toggleTheme(){ const t = document.documentElement.getAttribute('data-theme')==='dark'?'light':'dark'; document.documentElement.setAttribute('data-theme',t); localStorage.setItem('hub_theme',t); }
    function toggleSidebar(){
      const sb = document.getElementById('sidebar');
      if(window.innerWidth<=768){ sb.classList.toggle('open'); document.getElementById('backdrop').classList.toggle('open'); }
      else { sb.classList.toggle('collapsed'); localStorage.setItem('sidebar_closed', sb.classList.contains('collapsed')); }
    }
    function toggleModal(id, o){ const el = document.getElementById(id); if(el) el.style.display = o ? 'flex':'none'; }
    const getStorage = k => { try { return JSON.parse(localStorage.getItem(k)||'[]'); } catch(e){ return []; } };
    const setStorage = (k,v) => { try { localStorage.setItem(k, JSON.stringify(v.slice(-1000))); } catch(e){} };

    function showToast(htmlMsg, autoHideMs = 6000){
      clearTimeout(toastTimer);
      const t = document.getElementById('toast');
      document.getElementById('toast-msg').innerHTML = htmlMsg;
      t.style.display = 'flex';
      if(autoHideMs > 0){
        toastTimer = setTimeout(() => { t.style.display = 'none'; }, autoHideMs);
      }
    }
    function hideToast(){ clearTimeout(toastTimer); document.getElementById('toast').style.display = 'none'; }

    function updateBookmarkCount(){
      const b = getStorage('bookmarked_news');
      const el = document.getElementById('saved-count');
      if(el) el.textContent = b.length;
    }

    function toggleBookmark(id){
      let b = getStorage('bookmarked_news');
      const idx = b.indexOf(id);
      const el = document.querySelector(`.feed-card[data-id="${id}"]`);
      if(idx >= 0){
        b.splice(idx, 1);
        if(el) el.classList.remove('bookmarked');
      } else {
        b.push(id);
        if(el) el.classList.add('bookmarked');
      }
      setStorage('bookmarked_news', b);
      updateBookmarkCount();
      if(onlySaved) applyFilters();
    }

    function toggleSavedFilter(){
      onlySaved = !onlySaved;
      const btn = document.getElementById('saved-filter-btn');
      if(btn) btn.classList.toggle('active', onlySaved);
      applyFilters();
      const t = document.getElementById('current-title');
      if(t && onlySaved) {
        t.textContent = `🔖 ${getStorage('bookmarked_news').length} Gemerkte News`;
      } else if(t) {
        t.textContent = window.IS_ARCHIVE ? `Archiv: ${liveArticles.length} News bis ${buildTime}` : `${liveArticles.length} News bis ${buildTime}`;
      }
    }

    function toggleRead(id){
      let r = getStorage('read_news'); const idx = r.indexOf(id);
      const el = document.querySelector(`.feed-card[data-id="${id}"]`);
      if(idx>=0){ r.splice(idx,1); if(el) el.classList.remove('read'); } else { r.push(id); if(el) el.classList.add('read'); }
      setStorage('read_news', r);
    }
    function markAllRead(){
      const r = getStorage('read_news');
      document.querySelectorAll('.feed-card').forEach(c => { c.classList.add('read'); if(!r.includes(c.dataset.id)) r.push(c.dataset.id); });
      setStorage('read_news', r);
    }

    function renderUI(articles){
      let totalDups = 0; counts = {};
      articles.forEach(a => { totalDups += (a.other_sources||[]).length; counts[a.source||"Unbekannt"] = (counts[a.source||"Unbekannt"]||0) + 1; });
      const tEl = document.getElementById('current-title');
      if(tEl && !onlySaved) tEl.textContent = window.IS_ARCHIVE ? `Archiv: ${articles.length} News bis ${buildTime}` : `${articles.length} News bis ${buildTime}`;
      const dEl = document.getElementById('header-dup-info'); if(dEl) dEl.innerHTML = `🧹 ${totalDups} Duplikate`;

      const sorted = Array.from(new Set([...configuredSources, ...Object.keys(counts)])).sort((a,b)=>(counts[b]||0)-(counts[a]||0));
      const sl = document.getElementById('source-list');
      if(sl) {
        sl.innerHTML = `<li><button class="source-btn active" onclick="filterSource('all',this)"><span>Alle</span><span class="badge">${articles.length}</span></button></li>` +
          sorted.map(s => `<li><button class="source-btn" onclick="filterSource('${esc(s)}',this)"><span>${esc(s)}</span><span class="badge">${counts[s]||0}</span></button></li>`).join('');
      }

      const rList = getStorage('read_news'), sList = getStorage('seen_news'), bList = getStorage('bookmarked_news');
      updateBookmarkCount();

      const cont = document.getElementById('articles-container');
      if(cont) {
        cont.innerHTML = articles.map(a => {
          const id = hStr(a.link||''), oth = (a.other_sources&&a.other_sources.length)?`<span class="feed-others">• Auch bei: ${esc(a.other_sources.join(", "))}</span>`:'';
          const img = a.image ? `<img class="feed-thumb" src="${a.image}" loading="lazy" onerror="this.remove()">` : '';
          let cls = '';
          if(rList.includes(String(id))) cls += ' read';
          if(sList.includes(String(id))) cls += ' seen';
          if(bList.includes(String(id))) cls += ' bookmarked';

          return `<article class="feed-card${cls}" data-id="${id}" data-sources="${esc([a.source||'',...(a.other_sources||[])].join(';;;'))}">
            <div class="feed-content">
              <div class="feed-meta">
                <button class="unread-dot-btn" onclick="event.stopPropagation();toggleRead('${id}')" title="Als gelesen / ungelesen"><span class="unread-dot"></span></button>
                <button class="bookmark-btn" onclick="event.stopPropagation();toggleBookmark('${id}')" title="Für später merken">🔖</button>
                <span class="feed-source">${esc(a.source||'Quelle')}</span><span class="feed-time">${relTime(a.published)}</span>${oth}
              </div>
              <a class="feed-title" href="${esc(a.link||'#')}" target="_blank" rel="noopener" onclick="if(!getStorage('read_news').includes('${id}'))toggleRead('${id}')">${esc(a.title||'Ohne Titel')}</a>
              <p class="feed-summary">${a.summary||''}</p>
            </div>${img}
          </article>`;
        }).join('');
      }
    }

    function openDupModal(){
      const el = document.getElementById('dup-list'); if(!el) return;
      const m = {}; let tot = 0;
      liveArticles.forEach(a => {
        (a.merged_details||[]).forEach(d => { const s = d.source||"Unbekannt"; (m[s]=m[s]||[]).push(d); tot++; });
        (a.other_sources||[]).forEach(src => { if(!m[src]){ m[src]=[{source:src,title:"Altbestand ohne Einzeltitel",link:a.link,matched_with:a.title,is_legacy:true}]; tot++; } });
      });
      const entries = Object.entries(m).sort((a,b)=>b[1].length-a[1].length);
      el.innerHTML = !entries.length ? '<p style="color:var(--muted);padding:10px 0">Keine Duplikate gefunden.</p>' :
        `<div style="margin-bottom:10px;font-weight:600;color:var(--accent)">Gesamt: ${tot} bereinigte Berichte</div>` +
        entries.map(([src, items], i) => `<div style="border-bottom:1px solid var(--border);padding:6px 0">
          <div class="modal-row" style="border:none;cursor:pointer" onclick="const e=document.getElementById('dd-${i}');e.style.display=e.style.display==='none'?'block':'none'">
            <strong>${esc(src)}</strong><span class="badge">${items.length} ▾</span>
          </div>
          <div id="dd-${i}" style="display:none;padding-left:8px;border-left:2px solid var(--accent);margin-top:4px">
            ${items.map(it=>`<div style="margin-bottom:6px">${it.is_legacy?esc(it.title):`<a href="${esc(it.link)}" target="_blank" rel="noopener" style="color:var(--link);text-decoration:none">🔗 ${esc(it.title)}</a>`}<div style="color:var(--muted);font-size:.72rem">↳ Mit: "${esc(it.matched_with)}"</div></div>`).join('')}
          </div>
        </div>`).join('');
      toggleModal('dup-modal', true);
    }

    function filterSource(src, btn){
      activeSource = src; document.querySelectorAll('.source-btn').forEach(b => b.classList.remove('active'));
      if(btn) btn.classList.add('active');
      const t = document.getElementById('current-title');
      if(t && !onlySaved) t.textContent = (src==='all')?(window.IS_ARCHIVE?`Archiv: ${liveArticles.length} News bis ${buildTime}`:`${liveArticles.length} News bis ${buildTime}`):`${src} (${counts[src]||0}) bis ${buildTime}`;
      applyFilters(); if(window.innerWidth<=768) toggleSidebar();
    }
    function filterSearch(q){ clearTimeout(sTimer); sTimer = setTimeout(()=>{ searchQuery = q.toLowerCase().trim(); applyFilters(); }, 120); }
    function applyFilters(){
      const bList = getStorage('bookmarked_news');
      document.querySelectorAll('.feed-card').forEach(c => {
        const id = c.dataset.id;
        const mSaved = !onlySaved || bList.includes(id);
        const mSrc = activeSource==='all' || (c.dataset.sources||'').split(';;;').includes(activeSource);
        const mQ = !searchQuery || c.textContent.toLowerCase().includes(searchQuery);
        c.style.display = (mSaved && mSrc && mQ) ? '' : 'none';
      });
    }

    function triggerWorkflow(){
      const tk = localStorage.getItem('gh_token');
      if(!tk){
        showToast(`
          <div style="display:flex;flex-direction:column;gap:6px">
            <span>GitHub PAT eingeben:</span>
            <div style="display:flex;gap:6px">
              <input type="password" id="toast-token-inp" class="search-input" style="padding:4px 8px;font-size:.8rem" placeholder="ghp_...">
              <button class="btn" style="font-size:.8rem;padding:4px 8px" onclick="saveToastToken()">OK</button>
            </div>
          </div>
        `, 0);
      } else {
        dispatchWorkflow(tk);
      }
    }

    function saveToastToken(){
      const el = document.getElementById('toast-token-inp');
      if(el && el.value.trim()){
        const val = el.value.trim();
        localStorage.setItem('gh_token', val);
        dispatchWorkflow(val);
      }
    }

    async function dispatchWorkflow(tk){
      showToast('🚀 Starte Workflow im Hintergrund...', 0);
      try {
        const r = await fetch('https://api.github.com/repos/schoerb/news-hub/actions/workflows/deploy.yml/dispatches', {
          method:'POST',
          headers:{'Accept':'application/vnd.github+json','Authorization':'Bearer ' + tk},
          body:JSON.stringify({ref:'main'})
        });
        if(r.status === 204){
          showToast('✅ Workflow gestartet! <a href="https://github.com/schoerb/news-hub/actions" target="_blank" rel="noopener">Actions ↗</a>', 8000);
        } else {
          showToast(`⚠️ Fehler (HTTP ${r.status}). <a href="#" onclick="localStorage.removeItem('gh_token');triggerWorkflow();return false;">Token ändern</a>`, 8000);
        }
      } catch(e){
        showToast('❌ Netzwerkfehler: ' + esc(e.message), 6000);
      }
    }

    function parsePayload(pw){
      try {
        if(!rawData) return null;
        if(rawData.trim().startsWith('[')) return JSON.parse(rawData);
        if(!pw) return null;
        const dec = CryptoJS.AES.decrypt(rawData, pw).toString(CryptoJS.enc.Utf8);
        if(!dec || !dec.startsWith('[')) return null;
        return JSON.parse(dec);
      } catch(e){ return null; }
    }

    async function init(){
      initTheme();
      if(window.innerWidth>768 && localStorage.getItem('sidebar_closed')==='true') document.getElementById('sidebar').classList.add('collapsed');
      try {
        const r = await fetch('data.json?t=' + Date.now(), {cache:'no-store'}).catch(() => fetch('data.json'));
        if(!r.ok) throw new Error(r.status);
        rawData = await r.text();
      } catch(e){ document.getElementById('current-title').textContent = "Fehler beim Laden von data.json"; return; }

      let parsed = parsePayload('');
      if(!parsed) {
        const savedPw = localStorage.getItem('hub_key');
        if(savedPw) parsed = parsePayload(savedPw);
      }

      if(parsed) {
        globalArticles = parsed;
        onLoaded();
      } else {
        document.getElementById('auth-overlay').style.display = 'flex';
        document.getElementById('auth-pwd').focus();
      }
    }

    function submitAuth(){
      const pw = document.getElementById('auth-pwd').value;
      const parsed = parsePayload(pw);
      if(parsed) {
        localStorage.setItem('hub_key', pw);
        globalArticles = parsed;
        onLoaded();
      } else {
        document.getElementById('auth-err').style.display = 'block';
      }
    }

    function onLoaded(){
      try {
        document.getElementById('auth-overlay').style.display = 'none';
        const valid = new Set(globalArticles.map(a => String(hStr(a.link||''))));
        setStorage('read_news', getStorage('read_news').filter(id => valid.has(id)));
        setStorage('seen_news', getStorage('seen_news').filter(id => valid.has(id)));
        setStorage('bookmarked_news', getStorage('bookmarked_news').filter(id => valid.has(id)));

        const now = Date.now(), c24 = new Date(now - 86400000), c48 = new Date(now - 172800000);
        liveArticles = globalArticles.filter(a => {
          if (!a.published) return !window.IS_ARCHIVE;
          const p = new Date(a.published);
          if (isNaN(p.getTime())) return !window.IS_ARCHIVE;
          return window.IS_ARCHIVE ? (p < c24 && p >= c48) : (p >= c24);
        });
        if(!window.IS_ARCHIVE && !liveArticles.length && globalArticles.length) liveArticles = globalArticles;

        renderUI(liveArticles);

        // Seen Observer
        const timers = new Map(), obs = new IntersectionObserver(ents => {
          ents.forEach(e => {
            const id = e.target.dataset.id; if(!id) return;
            if(e.isIntersecting){
              timers.set(id, setTimeout(() => {
                const s = getStorage('seen_news'); if(!s.includes(id)){ s.push(id); setStorage('seen_news', s); }
                e.target.classList.add('seen'); obs.unobserve(e.target);
              }, 1000));
            } else if(timers.has(id)){ clearTimeout(timers.get(id)); timers.delete(id); }
          });
        }, {root: document.querySelector('.main'), threshold: 0.6});
        document.querySelectorAll('.feed-card:not(.seen)').forEach(c => obs.observe(c));

        // Header Scroll
        let lastY = 0; const mEl = document.querySelector('.main'), hEl = document.querySelector('.stream-header');
        mEl.addEventListener('scroll', () => {
          const y = mEl.scrollTop;
          if(Math.abs(lastY-y) > 6 && document.activeElement !== document.getElementById('search-box')) {
            hEl.classList.toggle('header-hidden', y > lastY && y > 50);
            lastY = y;
          }
        }, {passive:true});

        const hl = document.getElementById('health-list');
        if(hl) {
          hl.innerHTML = feedHealth.map(f => {
            const ok = f.status==='ok'||f.code===304||f.code===200;
            return `<div class="modal-row"><span>${ok?'🟢':'🔴'} ${esc(f.title)}</span><span style="color:${ok?'var(--muted)':'#ef4444'};font-family:monospace">${f.code||f.status}</span></div>`;
          }).join('');
        }
      } catch(err) {
        console.error(err);
        document.getElementById('current-title').textContent = "Fehler: " + err.message;
      }
    }

    document.addEventListener('visibilitychange', () => {
      if(document.visibilityState==='visible'){
        document.querySelectorAll('.feed-card').forEach(c => {
          const art = liveArticles.find(a => String(hStr(a.link||'')) === c.dataset.id);
          if(art && art.published) c.querySelector('.feed-time').textContent = relTime(art.published);
        });
        fetch('data.json?t=' + Date.now(), {cache:'no-store'}).then(r => r.text()).then(t => { 
          if(t && t !== rawData){ 
            rawData = t; 
            const parsed = parsePayload(localStorage.getItem('hub_key')||"");
            if(parsed) { globalArticles = parsed; onLoaded(); }
          } 
        });
      }
    });

    document.addEventListener('keydown', e => {
      if(document.activeElement === document.getElementById('search-box')) return;
      const vis = Array.from(document.querySelectorAll('.feed-card')).filter(c => c.style.display !== 'none');
      if(e.key==='[') toggleSidebar();
      if(e.key==='j' && vis.length){ sIndex = Math.min(sIndex+1, vis.length-1); vis[sIndex].scrollIntoView({behavior:'smooth',block:'nearest'}); }
      if(e.key==='k' && vis.length){ sIndex = Math.max(sIndex-1, 0); vis[sIndex].scrollIntoView({behavior:'smooth',block:'nearest'}); }
      if(e.key==='o' && sIndex>=0) window.open(vis[sIndex].querySelector('.feed-title').href, '_blank');
      if(e.key==='m' && sIndex>=0) toggleRead(vis[sIndex].dataset.id);
      if(e.key==='b' && sIndex>=0) toggleBookmark(vis[sIndex].dataset.id);
      if(e.key==='/'){ e.preventDefault(); document.getElementById('search-box').focus(); }
      if(e.key==='Escape'){ toggleModal('health-modal',false); toggleModal('dup-modal',false); hideToast(); }
    });

    document.addEventListener('DOMContentLoaded', init);
  </script>
</body>
</html>
"""


def render_page(feed_health, feeds, is_archive=False):
    now_str = datetime.datetime.now(BERLIN_TZ).strftime("%d.%m.%Y %H:%M")
    ok = sum(1 for h in feed_health if h["status"] == "ok" or h["code"] in (200, 304))
    failed = len(feed_health) - ok
    h_text = f'<span style="color:#eab308;cursor:pointer" onclick="toggleModal(\'health-modal\',true)">🟡 {ok}/{len(feed_health)} Feeds ({failed} gestört)</span>' if failed > 0 else f'<span class="meta-clickable" onclick="toggleModal(\'health-modal\',true)">🟢 {ok}/{len(feed_health)} Feeds online</span>'

    return PAGE_TEMPLATE.replace("__PAGE_TITLE__", "Archiv" if is_archive else "News-Hub") \
                         .replace("__SIDEBAR_TITLE__", "Archiv (24–48h)" if is_archive else "News-Hub") \
                         .replace("__NAV_TARGET_URL__", "index.html" if is_archive else "archive.html") \
                         .replace("__NAV_TARGET_TEXT__", "← Zum Live-Feed" if is_archive else "📑 Zum Archiv (24–48h)") \
                         .replace("__MARK_ALL_BTN__", "" if is_archive else '<button class="source-btn" style="text-align:center;background:var(--border)" onclick="markAllRead()">✓ Alle gelesen</button>') \
                         .replace("__DESKTOP_REFRESH_BTN__", "" if is_archive else '<button class="btn" onclick="triggerWorkflow()">🔄</button>') \
                         .replace("__NOW_STR__", now_str) \
                         .replace("__HEALTH_BLOCK__", h_text) \
                         .replace("__HEALTH_DATA__", json.dumps(feed_health, ensure_ascii=False)) \
                         .replace("__CONFIGURED_SOURCES__", json.dumps([f["title"] for f in feeds], ensure_ascii=False)) \
                         .replace("__IS_ARCHIVE__", "true" if is_archive else "false")


if __name__ == "__main__":
    os.makedirs("public", exist_ok=True)
    pw = os.environ.get("PAGE_PASSWORD", "")
    cutoff_ts = int((datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=MAX_RETENTION_HOURS)).timestamp())

    cached, meta = load_cached_state()
    cached = [a for a in cached if a.get("_ts", 0) > cutoff_ts]
    feeds = parse_opml()

    raw_items, new_meta, feed_health = fetch_all_feeds(feeds, meta)
    with open("cache_meta.json", "w", encoding="utf-8") as f:
        json.dump(new_meta, f, separators=(',', ':'))

    new_items = []
    for r in raw_items:
        if any(r["link"] == c["link"] for c in cached):
            continue
        m = next((c for c in cached if is_dup(r["title"], c["title"], extract_features(r["title"]), extract_features(c["title"]))), None)
        if m:
            if r["source"] != m["source"] and r["source"] not in m.setdefault("other_sources", []):
                m["other_sources"].append(r["source"])
            m.setdefault("merged_details", []).append({"source": r["source"], "title": r["title"], "link": r["link"], "matched_with": m["title"]})
        else:
            new_items.append(r)

    print(f"📦 Neue Unikate: {len(new_items)} (Cache: {len(cached)})")
    bundled = consolidate_articles(new_items)
    combined = (summarize_delta_with_gemini(bundled) + cached) if bundled else cached
    final = sorted(consolidate_articles(combined), key=lambda a: a.get("_ts", 0), reverse=True)

    frontend_data = [{
        "title": a["title"], "link": a["link"], "source": a["source"], "summary": a["summary"],
        "published": a.get("published"), **({"image": a["image"]} if a.get("image") else {}),
        **({"other_sources": a["other_sources"]} if a.get("other_sources") else {}),
        **({"merged_details": a["merged_details"]} if a.get("merged_details") else {})
    } for a in final]

    if "GITHUB_OUTPUT" in os.environ:
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as gh:
            gh.write(f"deploy={'true' if bool(bundled) or len(cached) != len(final) else 'false'}\n")

    json_payload = json.dumps(frontend_data, ensure_ascii=False, separators=(',', ':'))
    with open("public/data.json", "w", encoding="utf-8") as f:
        f.write(encrypt_payload(json_payload, pw) if pw else json_payload)

    with open("public/index.html", "w", encoding="utf-8") as f:
        f.write(render_page(feed_health, feeds, is_archive=False))

    with open("public/archive.html", "w", encoding="utf-8") as f:
        f.write(render_page(feed_health, feeds, is_archive=True))
