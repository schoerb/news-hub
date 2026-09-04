import base64
import datetime
import hashlib
import html
import json
import os
import re
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
import zoneinfo
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
import feedparser
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
import requests

# --- Konfiguration ---
DEFAULT_PRIO = 1
MAX_RETENTION_HOURS = 48
REMOTE_DATA_URL = "https://schoerb.github.io/news-hub/data.json"
BERLIN_TZ = zoneinfo.ZoneInfo("Europe/Berlin")

STOPWORDS = {
    # Deutsch
    "im", "in", "der", "die", "das", "den", "dem", "des", "für", "von", "mit", "ab", "sofort",
    "neu", "neue", "neues", "neuen", "neuer", "update", "bringt", "startet", "erhält", "offiziell",
    "jetzt", "nach", "zum", "zur", "wie", "auf", "ein", "eine", "einen", "einem", "einer",
    "als", "sich", "nicht", "auch", "über", "test", "getestet", "bericht", "schlägt", "überzeugt",
    "zeigt", "soll", "gibt", "erstes", "erste", "erster", "download", "verfügbar", "rollt", "aus",
    "wird", "kann", "haben", "mehr", "dieses", "dieser", "diese",
    # Englisch
    "the", "a", "an", "and", "or", "in", "on", "at", "to", "for", "of", "with", "by", "from",
    "is", "are", "was", "were", "be", "has", "have", "had", "will", "can", "how", "what",
    "new", "gets", "brings", "adds", "rolls", "out", "now", "available", "first", "look", "review"
}

# --- Thread-Safe Session Provider ---
_thread_local = threading.local()

def get_session():
    if not hasattr(_thread_local, "session"):
        s = requests.Session()
        s.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Accept": "application/rss+xml, application/xml, text/xml, text/html, application/xhtml+xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
            "Sec-Ch-Ua": '"Not/A)Brand";v="8", "Chromium";v="126", "Google Chrome";v="126"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "cross-site",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        })
        _thread_local.session = s
    return _thread_local.session


# --- Krypto-Helfer ---
def openssl_kdf(password: bytes, salt: bytes, key_len=32, iv_len=16) -> tuple[bytes, bytes]:
    d = b""
    d_i = b""
    while len(d) < (key_len + iv_len):
        d_i = hashlib.md5(d_i + password + salt).digest()
        d += d_i
    return d[:key_len], d[key_len:key_len + iv_len]


def encrypt_payload(data_str: str, password: str) -> str:
    if not password:
        return data_str

    salt = os.urandom(8)
    key, iv = openssl_kdf(password.encode("utf-8"), salt)

    pad_len = 16 - (len(data_str.encode("utf-8")) % 16)
    padded_data = data_str.encode("utf-8") + bytes([pad_len] * pad_len)

    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(padded_data) + encryptor.finalize()

    return base64.b64encode(b"Salted__" + salt + ciphertext).decode("utf-8")


def decrypt_payload(enc_str: str, password: str) -> str:
    if not password:
        return enc_str
    try:
        raw = base64.b64decode(enc_str)
        if not raw.startswith(b"Salted__"):
            return enc_str
        salt = raw[8:16]
        ciphertext = raw[16:]
        key, iv = openssl_kdf(password.encode("utf-8"), salt)

        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        decryptor = cipher.decryptor()
        padded_data = decryptor.update(ciphertext) + decryptor.finalize()
        pad_len = padded_data[-1]
        return padded_data[:-pad_len].decode("utf-8")
    except Exception:
        return ""


def hash_feed_url(url: str) -> str:
    salt = os.environ.get("PAGE_PASSWORD", "static_news_salt")
    return hashlib.sha256((url + salt).encode("utf-8")).hexdigest()[:16]


def get_private_priorities() -> dict:
    raw = os.environ.get("FEED_PRIORITIES", "").strip()
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            pass
    return {}


# --- Pydantic Schemas für Gemini ---
class DeltaItem(BaseModel):
    id: int = Field(description="Index des Artikels aus dem Batch")
    title: str = Field(description="Präziser deutscher Titel ohne Clickbait. Falls Original englisch: exakt und sachlich ins Deutsche übersetzen.")
    summary: str = Field(description="Genau 1 prägnanter deutscher Satz. Schlüsselbegriffe mit **fett** hervorheben")
    use_image: bool = Field(default=False, description="True NUR wenn das Bild ein konkretes Gerät, UI-Element oder einen Chart zeigt")


class DeltaBatchResponse(BaseModel):
    items: list[DeltaItem]


def clean_url(url: str) -> str:
    if not url:
        return ""
    p = urllib.parse.urlsplit(url)
    q = [
        (k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True)
        if not (k.startswith("utm_") or k in ("wt_mc", "fbclid", "ref", "source"))
    ]
    return urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, urllib.parse.urlencode(q), p.fragment))


def parse_opml():
    raw_opml = os.environ.get("FEEDS_OPML", "").strip()
    priorities = get_private_priorities()

    if raw_opml:
        try:
            tree = ET.fromstring(raw_opml)
        except Exception:
            return []
    elif os.path.exists("feeds.opml"):
        try:
            tree = ET.parse("feeds.opml").getroot()
        except Exception:
            return []
    else:
        return []

    feeds = []
    for node in tree.findall(".//outline[@xmlUrl]"):
        source_name = (node.get("text") or node.get("title") or "Feed").strip()
        url = node.get("xmlUrl", "").strip()
        if url:
            prio_attr = node.get("priority")
            if prio_attr and prio_attr.isdigit():
                prio = int(prio_attr)
            else:
                prio = priorities.get(source_name, DEFAULT_PRIO)

            feeds.append({
                "title": source_name,
                "url": url,
                "priority": prio,
            })
    return feeds


def is_generic_badge_or_tracker(url: str) -> bool:
    if not url:
        return True
    lower = url.lower()
    blocklist = ["favicon", "avatar", "logo", "pixel", "tracking", "1x1", "badge", "icon", "share-buttons"]
    return any(b in lower for b in blocklist)


def extract_image(entry):
    for key in ("media_content", "media_thumbnail"):
        if key in entry and entry[key]:
            u = entry[key][0].get("url")
            if u and not is_generic_badge_or_tracker(u):
                return u

    for enc in entry.get("enclosures", []):
        if enc.get("type", "").startswith("image/"):
            u = enc.get("href")
            if u and not is_generic_badge_or_tracker(u):
                return u

    content = entry.get("summary", "")
    if "content" in entry and entry.content:
        content += entry.content[0].get("value", "")

    m = re.search(r'<img[^>]+src=["\']?([^\s"\'<>]+\.(?:jpg|jpeg|png|webp))', content, re.I)
    if m and not is_generic_badge_or_tracker(m.group(1)):
        return m.group(1)

    return None


def parse_timestamp(iso_or_obj) -> int:
    if isinstance(iso_or_obj, (int, float)):
        return int(iso_or_obj)
    if not iso_or_obj:
        return 0
    try:
        dt = datetime.datetime.fromisoformat(str(iso_or_obj).replace("Z", "+00:00"))
        return int(dt.timestamp())
    except Exception:
        return 0


def load_cached_state():
    articles = []
    cache_meta = {}
    password = os.environ.get("PAGE_PASSWORD", "")

    if os.path.exists("public/data.json"):
        try:
            with open("public/data.json", "r", encoding="utf-8") as f:
                content = f.read()
                if password:
                    content = decrypt_payload(content, password)
                articles = json.loads(content)
        except Exception:
            pass
    elif REMOTE_DATA_URL:
        try:
            r = get_session().get(REMOTE_DATA_URL, timeout=4)
            if r.ok:
                content = r.text
                if password:
                    content = decrypt_payload(content, password)
                articles = json.loads(content)
        except Exception:
            pass

    if os.path.exists("cache_meta.json"):
        try:
            with open("cache_meta.json", "r", encoding="utf-8") as f:
                cache_meta = json.load(f)
        except Exception:
            pass

    for a in articles:
        a["_ts"] = parse_timestamp(a.get("published"))

    return articles, cache_meta


def fetch_all_feeds(feeds, cache_meta):
    now = datetime.datetime.now(datetime.timezone.utc)
    cutoff_ts = int((now - datetime.timedelta(hours=MAX_RETENTION_HOURS)).timestamp())
    new_cache_meta = dict(cache_meta)
    feed_health = []

    def _fetch(f):
        url = f["url"]
        url_key = hash_feed_url(url)
        headers = {}
        if url_key in cache_meta:
            if "etag" in cache_meta[url_key]:
                headers["If-None-Match"] = cache_meta[url_key]["etag"]
            if "modified" in cache_meta[url_key]:
                headers["If-Modified-Since"] = cache_meta[url_key]["modified"]

        try:
            parsed_domain = urllib.parse.urlsplit(url)
            if parsed_domain.scheme and parsed_domain.netloc:
                headers["Referer"] = f"{parsed_domain.scheme}://{parsed_domain.netloc}/"

            r = get_session().get(url, headers=headers, timeout=10)

            if r.status_code == 304:
                return ([], {"title": f["title"], "status": "ok", "code": 304})

            if not r.ok and r.status_code != 304:
                return ([], {"title": f["title"], "status": "error", "code": r.status_code})

            meta = {}
            if "etag" in r.headers:
                meta["etag"] = r.headers["etag"]
            if "last-modified" in r.headers:
                meta["modified"] = r.headers["last-modified"]
            if meta:
                new_cache_meta[url_key] = meta

            parsed = feedparser.parse(r.content)
            if parsed.bozo and not parsed.entries:
                return ([], {"title": f["title"], "status": "parse_error", "code": r.status_code})

            items = []
            for e in parsed.entries:
                entry_ts = None
                pub_iso = None
                if hasattr(e, "published_parsed") and e.published_parsed:
                    dt = datetime.datetime(*e.published_parsed[:6], tzinfo=datetime.timezone.utc)
                    entry_ts = int(dt.timestamp())
                    pub_iso = dt.isoformat()
                elif hasattr(e, "updated_parsed") and e.updated_parsed:
                    dt = datetime.datetime(*e.updated_parsed[:6], tzinfo=datetime.timezone.utc)
                    entry_ts = int(dt.timestamp())
                    pub_iso = dt.isoformat()
                else:
                    entry_ts = int(now.timestamp())
                    pub_iso = now.isoformat()

                if entry_ts > cutoff_ts:
                    summary = re.sub(r"<[^>]+>", " ", e.get("summary", ""))
                    items.append({
                        "title": e.title.strip(),
                        "summary": " ".join(summary.split())[:350],
                        "link": clean_url(e.link.strip()),
                        "image": extract_image(e),
                        "source": f["title"].strip(),
                        "priority": f["priority"],
                        "published": pub_iso,
                        "_ts": entry_ts,
                    })
            return (items, {"title": f["title"], "status": "ok", "code": r.status_code})
        except Exception:
            return ([], {"title": f["title"], "status": "exception", "code": "timeout/conn"})

    all_items = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        results = list(ex.map(_fetch, feeds))
        for items, health in results:
            all_items.extend(items)
            feed_health.append(health)

    return all_items, new_cache_meta, feed_health


# --- Deduplizierung & Stemming ---
def clean_stem(word: str) -> str:
    w = word.lower().strip()
    for ending in ("s", "n", "en", "er", "es", "e"):
        if w.endswith(ending) and len(w) > 4:
            return w[:-len(ending)]
    return w


def extract_keywords(title: str) -> tuple[set, set]:
    clean = re.sub(r"[^\w\s\.]", " ", title.lower())
    words = clean.split()
    numbers = {w for w in words if any(char.isdigit() for char in w) and len(w) >= 2}
    keywords = {clean_stem(w) for w in words if w not in STOPWORDS and len(w) > 2 and w not in numbers}
    return keywords, numbers


def is_duplicate(title_a: str, title_b: str, memo_a=None, memo_b=None) -> bool:
    kw_a, num_a = memo_a if memo_a else extract_keywords(title_a)
    kw_b, num_b = memo_b if memo_b else extract_keywords(title_b)

    if (num_a & num_b) and (kw_a & kw_b):
        return True

    sub_matches = set()
    for wa in kw_a:
        for wb in kw_b:
            if len(wa) >= 4 and len(wb) >= 4 and wa[:4] == wb[:4]:
                sub_matches.add(wa[:4])

    combined_overlap = len((kw_a & kw_b) | sub_matches)
    min_len = min(len(kw_a), len(kw_b))

    if combined_overlap >= 3 or (min_len > 0 and (combined_overlap / min_len) >= 0.33):
        return True

    clean_a = re.sub(r"[^\w\s]", "", title_a.lower())
    clean_b = re.sub(r"[^\w\s]", "", title_b.lower())
    return SequenceMatcher(None, clean_a, clean_b).ratio() >= 0.60


def consolidate_articles(articles: list[dict]) -> list[dict]:
    sorted_arts = sorted(articles, key=lambda x: x.get("priority", DEFAULT_PRIO), reverse=True)
    unique_list = []
    cached_features = []

    for item in sorted_arts:
        feat = extract_keywords(item["title"])
        match = None
        for idx, existing in enumerate(unique_list):
            if is_duplicate(item["title"], existing["title"], feat, cached_features[idx]):
                match = existing
                break

        if match:
            others = match.setdefault("other_sources", [])
            src = item.get("source")
            if src and src != match.get("source") and src not in others:
                others.append(src)
        else:
            item_copy = dict(item)
            item_copy.setdefault("other_sources", [])
            unique_list.append(item_copy)
            cached_features.append(feat)

    return unique_list


def summarize_chunk_with_gemini(client, chunk_items, max_retries=3):
    payload = [
        {
            "id": idx,
            "title": a["title"],
            "source": a["source"],
            "text": a["summary"],
            "has_image": bool(a.get("image")),
        }
        for idx, a in enumerate(chunk_items)
    ]

    prompt = f"""
Optimiere und fasse diese Tech-Artikel zusammen:
1. 'title': Formuliere eine präzise, aussagekräftige Überschrift auf DEUTSCH (kein Clickbait, nenne konkrete Produktnamen, Versionsnummern oder Fakten). Falls der Quelltitel englisch ist, übersetze ihn sinngemäß und prägnant ins Deutsche.
2. 'summary': Genau 1 prägnanter deutscher Satz. Hebe die wichtigsten 2-3 Schlüsselbegriffe mit **fett** hervor.
3. 'use_image': True NUR wenn das Bild ein echtes Produkt, ein UI-Element oder einen Chart/Benchmark zeigt.

Artikel:
{json.dumps(payload, ensure_ascii=False)}
"""

    models = ["gemini-3.6-flash", "gemini-3.5-flash-lite"]

    for attempt in range(max_retries):
        selected_model = models[min(attempt, len(models) - 1)]
        try:
            res = client.models.generate_content(
                model=selected_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.2,
                    response_mime_type="application/json",
                    response_schema=DeltaBatchResponse,
                ),
            )
            parsed = DeltaBatchResponse.model_validate_json(res.text)

            processed = []
            for item in parsed.items:
                if 0 <= item.id < len(chunk_items):
                    orig = chunk_items[item.id]
                    clean_title = html.escape(item.title.strip())
                    clean_sum = html.escape(item.summary.strip())
                    clean_sum = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", clean_sum)

                    processed.append({
                        "title": clean_title or orig["title"],
                        "link": orig["link"],
                        "source": orig["source"],
                        "other_sources": orig.get("other_sources", []),
                        "summary": clean_sum,
                        "image": orig["image"] if item.use_image else None,
                        "published": orig["published"],
                        "_ts": orig.get("_ts", 0),
                    })
            return processed
        except Exception:
            wait_time = (2 ** attempt) * 4
            time.sleep(wait_time)

    return [
        {
            "title": o["title"],
            "link": o["link"],
            "source": o["source"],
            "other_sources": o.get("other_sources", []),
            "summary": html.escape(o["summary"]),
            "image": o["image"],
            "published": o["published"],
            "_ts": o.get("_ts", 0),
        }
        for o in chunk_items
    ]


def summarize_delta_with_gemini(new_items):
    if not new_items:
        return []

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return []

    client = genai.Client(api_key=api_key)
    chunk_size = 25
    chunks = [new_items[i : i + chunk_size] for i in range(0, len(new_items), chunk_size)]

    if len(chunks) == 1:
        return summarize_chunk_with_gemini(client, chunks[0])

    all_processed = []
    with ThreadPoolExecutor(max_workers=min(len(chunks), 3)) as ex:
        futures = [ex.submit(summarize_chunk_with_gemini, client, c) for c in chunks]
        for f in futures:
            all_processed.extend(f.result())

    return all_processed


def expire_old_articles(articles):
    cutoff_ts = int((datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=MAX_RETENTION_HOURS)).timestamp())
    return [a for a in articles if a.get("_ts", 0) > cutoff_ts]


# --- Gemeinsame UI-Komponenten (CSS, Modals, Core-JS) ---
SHARED_CSS = """
    :root {
      --bg: #121418;
      --sidebar-bg: #181b20;
      --card-bg: #1e2229;
      --card-hover: #262b34;
      --border: #2e3440;
      --text: #e2e8f0;
      --text-muted: #94a3b8;
      --text-bold: #f1f5f9;
      --accent: #2ecc71;
      --accent-dim: rgba(46, 204, 113, 0.15);
      --link: #60a5fa;
      --focus-ring: #3b82f6;
    }

    [data-theme="light"] {
      --bg: #f8fafc;
      --sidebar-bg: #ffffff;
      --card-bg: #ffffff;
      --card-hover: #f1f5f9;
      --border: #e2e8f0;
      --text: #1e293b;
      --text-muted: #64748b;
      --text-bold: #0f172a;
      --accent: #16a34a;
      --accent-dim: rgba(22, 163, 74, 0.12);
      --link: #2563eb;
      --focus-ring: #2563eb;
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background-color: var(--bg);
      color: var(--text);
      display: flex;
      height: 100vh;
      overflow: hidden;
      transition: background-color 0.2s ease, color 0.2s ease;
    }

    .modal-overlay {
      display: none;
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.75);
      backdrop-filter: blur(2px);
      z-index: 1100;
      align-items: center;
      justify-content: center;
      padding: 16px;
    }
    .modal-card {
      background: var(--sidebar-bg);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 24px;
      width: 100%;
      max-width: 480px;
      max-height: 80vh;
      display: flex;
      flex-direction: column;
      box-shadow: 0 16px 36px rgba(0,0,0,0.3);
    }
    .modal-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 16px;
      border-bottom: 1px solid var(--border);
      padding-bottom: 10px;
    }
    .modal-header h2 { font-size: 1.15rem; color: var(--text-bold); }
    .modal-body { overflow-y: auto; flex-grow: 1; font-size: 0.88rem; }
    .modal-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 10px 0;
      border-bottom: 1px solid var(--border);
      gap: 12px;
    }
    .modal-close-btn {
      width: 100%;
      background: var(--accent);
      color: #fff;
      border: none;
      padding: 12px;
      border-radius: 6px;
      font-weight: 600;
      cursor: pointer;
      margin-top: 16px;
    }

    .sidebar-backdrop {
      display: none;
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.65);
      backdrop-filter: blur(2px);
      z-index: 90;
    }

    .sidebar {
      width: 290px;
      background: var(--sidebar-bg);
      border-right: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      flex-shrink: 0;
      z-index: 100;
      transition: width 0.25s cubic-bezier(0.4, 0, 0.2, 1), transform 0.25s cubic-bezier(0.4, 0, 0.2, 1);
      overflow: hidden;
      white-space: nowrap;
    }
    .sidebar.collapsed { width: 0px; border-right: none; }
    .sidebar-header {
      padding: 20px;
      border-bottom: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      min-width: 290px;
    }
    .sidebar-header h1 { font-size: 1.15rem; font-weight: 700; color: var(--text-bold); }
    .close-btn {
      background: none;
      border: none;
      color: var(--text-muted);
      font-size: 1.4rem;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      line-height: 1;
    }
    .close-btn:hover { color: var(--text); }
    .source-list { list-style: none; padding: 12px; overflow-y: auto; flex-grow: 1; min-width: 290px; }
    .source-btn {
      width: 100%;
      text-align: left;
      padding: 10px 14px;
      margin-bottom: 4px;
      border-radius: 6px;
      background: transparent;
      border: none;
      color: var(--text-muted);
      font-size: 0.85rem;
      font-weight: 500;
      cursor: pointer;
      display: flex;
      justify-content: space-between;
      align-items: center;
      transition: all 0.15s ease;
    }
    .source-btn:hover, .source-btn.active {
      background: var(--accent-dim);
      color: var(--accent);
      font-weight: 600;
    }
    .badge {
      background: var(--border);
      padding: 2px 8px;
      border-radius: 12px;
      font-size: 0.7rem;
      color: var(--text);
    }
    .source-btn.active .badge { background: var(--accent); color: #fff; }

    .sidebar-footer { padding: 16px; border-top: 1px solid var(--border); min-width: 290px; }
    .archive-link-btn {
      display: block;
      text-align: center;
      color: var(--accent);
      text-decoration: none;
      font-size: 0.82rem;
      font-weight: 600;
      padding: 8px;
      border-radius: 6px;
      background: var(--accent-dim);
    }

    .main {
      flex-grow: 1;
      overflow-y: auto;
      padding: 28px 40px;
      max-width: 100%;
      transition: padding 0.2s ease;
    }

    .stream-header {
      margin-bottom: 24px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
      border-bottom: 1px solid var(--border);
      padding-bottom: 16px;
    }
    .header-left { display: flex; align-items: center; gap: 12px; }
    .header-right { display: flex; align-items: center; gap: 8px; }
    .stream-header h2 { font-size: 1.4rem; font-weight: 700; color: var(--text-bold); }

    .theme-toggle, .menu-toggle {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 6px;
      color: var(--text);
      font-size: 1.1rem;
      padding: 6px 10px;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      transition: background 0.15s, border-color 0.15s;
    }
    .theme-toggle:hover, .menu-toggle:hover {
      background: var(--card-hover);
      border-color: var(--text-muted);
    }

    .search-input {
      background: var(--card-bg);
      border: 1px solid var(--border);
      color: var(--text);
      padding: 8px 14px;
      border-radius: 6px;
      font-size: 0.85rem;
      outline: none;
      width: 240px;
      transition: border-color 0.2s;
    }
    .search-input:focus { border-color: var(--accent); }

    .cards-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
      gap: 18px;
      align-items: stretch;
    }

    .feed-card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 18px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      transition: transform 0.15s ease, border-color 0.15s ease, opacity 0.2s, box-shadow 0.15s ease;
      outline: none;
    }
    .feed-card:hover {
      background: var(--card-hover);
      border-color: var(--text-muted);
      transform: translateY(-2px);
    }
    .feed-card.selected {
      border-color: var(--focus-ring);
      box-shadow: 0 0 0 2px var(--focus-ring);
    }
    .feed-card.read { opacity: 0.4; }
    .feed-card.read .feed-title { color: var(--text-muted); }

    .feed-content { display: flex; flex-direction: column; flex-grow: 1; }
    .feed-meta {
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 6px;
      font-size: 0.75rem;
      margin-bottom: 8px;
    }
    .feed-source { color: var(--accent); font-weight: 600; }
    .feed-time, .feed-others { color: var(--text-muted); font-size: 0.72rem; }

    .feed-title {
      font-size: 1.05rem;
      font-weight: 600;
      color: var(--text-bold);
      text-decoration: none;
      line-height: 1.4;
      margin-bottom: 8px;
    }
    .feed-title:hover { color: var(--link); text-decoration: underline; }

    .feed-summary {
      font-size: 0.88rem;
      color: var(--text-muted);
      line-height: 1.5;
      margin-bottom: 14px;
      flex-grow: 1;
    }
    .feed-summary strong { color: var(--text-bold); font-weight: 600; }

    .feed-thumb {
      width: 100%;
      height: 160px;
      object-fit: cover;
      border-radius: 6px;
      margin-top: auto;
      background: var(--border);
    }

    @media (max-width: 768px) {
      .sidebar {
        position: fixed;
        inset: 0 auto 0 0;
        width: 290px !important;
        transform: translateX(-100%);
        box-shadow: 4px 0 24px rgba(0,0,0,0.6);
      }
      .sidebar.open { transform: translateX(0); }
      .sidebar.collapsed { width: 290px; }
      .sidebar-backdrop.open { display: block; }
      .main { padding: 14px 12px; }
      .stream-header { flex-direction: column; align-items: stretch; gap: 12px; }
      .header-right { width: 100%; }
      .search-input { flex-grow: 1; width: auto; }
      .cards-grid { grid-template-columns: 1fr; gap: 12px; }
      .feed-card { padding: 14px; }
      .feed-thumb { height: 150px; }
      .shortcuts-hint { display: none; }
    }
"""

SHARED_MODALS = """
  <!-- Feed Health Modal -->
  <div id="health-modal" class="modal-overlay">
    <div class="modal-card">
      <div class="modal-header">
        <h2>📡 Feed-Status Details</h2>
        <button class="close-btn" onclick="closeHealthModal()">&times;</button>
      </div>
      <div id="health-list" class="modal-body"></div>
      <button class="modal-close-btn" onclick="closeHealthModal()">Schließen</button>
    </div>
  </div>

  <!-- Duplicate Stats Modal -->
  <div id="duplicate-modal" class="modal-overlay">
    <div class="modal-card">
      <div class="modal-header">
        <h2>🧹 Bereinigte Duplikate nach Quelle</h2>
        <button class="close-btn" onclick="closeDuplicateModal()">&times;</button>
      </div>
      <p style="font-size:0.8rem; color:var(--text-muted); margin-bottom:12px;">
        Übersicht der Quellen, deren Doppelberichte gebündelt wurden:
      </p>
      <div id="duplicate-list" class="modal-body"></div>
      <button class="modal-close-btn" onclick="closeDuplicateModal()">Schließen</button>
    </div>
  </div>
"""

SHARED_JS = """
    function escapeHtml(s) {
      return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    function hashString(str) {
      let hash = 0;
      for (let i = 0; i < str.length; i++) {
        hash = ((hash << 5) - hash) + str.charCodeAt(i);
        hash |= 0;
      }
      return hash;
    }

    function formatRelativeTime(isoDateStr) {
      if (!isoDateStr) return '';
      try {
        const diffSec = Math.floor((new Date() - new Date(isoDateStr)) / 1000);
        if (isNaN(diffSec)) return '';
        if (diffSec < 60) return '• gerade eben';
        const m = Math.floor(diffSec / 60);
        if (m < 60) return `• vor ${m}m`;
        const h = Math.floor(m / 60);
        if (h < 24) return `• vor ${h}h`;
        return `• vor ${Math.floor(h / 24)}d`;
      } catch(e) { return ''; }
    }

    function initTheme() {
      const savedTheme = localStorage.getItem('hub_theme');
      if (savedTheme) {
        document.documentElement.setAttribute('data-theme', savedTheme);
      } else if (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches) {
        document.documentElement.setAttribute('data-theme', 'light');
      }
    }

    function toggleTheme() {
      const current = document.documentElement.getAttribute('data-theme') || 'dark';
      const target = current === 'dark' ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', target);
      localStorage.setItem('hub_theme', target);
    }

    function initSidebarState() {
      if (window.innerWidth > 768) {
        const isClosed = localStorage.getItem('sidebar_closed') === 'true';
        if (isClosed) document.getElementById('sidebar').classList.add('collapsed');
      }
    }

    function toggleSidebar() {
      const sidebar = document.getElementById('sidebar');
      const backdrop = document.getElementById('backdrop');
      if (window.innerWidth <= 768) {
        sidebar.classList.toggle('open');
        backdrop.classList.toggle('open');
      } else {
        sidebar.classList.toggle('collapsed');
        localStorage.setItem('sidebar_closed', sidebar.classList.contains('collapsed'));
      }
    }

    function openHealthModal() {
      const listEl = document.getElementById('health-list');
      if (!feedHealthData || !feedHealthData.length) {
        listEl.innerHTML = '<p style="color:var(--text-muted); padding:12px 0;">Keine Diagnosedaten vorhanden.</p>';
      } else {
        listEl.innerHTML = feedHealthData.map(f => {
          const isOk = f.status === 'ok' || f.code === 304 || f.code === 200;
          const icon = isOk ? '🟢' : '🔴';
          const info = (f.code === 304) ? 'HTTP 304 (Cache unverändert)' : (isOk ? `HTTP ${f.code}` : `Fehler: ${f.status} (${f.code})`);
          const color = isOk ? 'var(--text-muted)' : '#ef4444';
          return `
            <div class="modal-row">
              <span style="font-weight:500; color:var(--text);">${icon} ${escapeHtml(f.title)}</span>
              <span style="color:${color}; font-size:0.8rem; font-family:monospace;">${escapeHtml(info)}</span>
            </div>
          `;
        }).join('');
      }
      document.getElementById('health-modal').style.display = 'flex';
    }

    function closeHealthModal() {
      document.getElementById('health-modal').style.display = 'none';
    }

    function openDuplicateModal() {
      const listEl = document.getElementById('duplicate-list');
      const dupCounts = {};
      let totalDups = 0;

      activeArticlesCollection.forEach(a => {
        (a.other_sources || []).forEach(src => {
          dupCounts[src] = (dupCounts[src] || 0) + 1;
          totalDups++;
        });
      });

      const sortedDups = Object.entries(dupCounts).sort((a, b) => b[1] - a[1]);
      if (!sortedDups.length) {
        listEl.innerHTML = '<p style="color:var(--text-muted); padding:12px 0;">Keine zusammengeführten Duplikate im aktuellen Datenbestand.</p>';
      } else {
        listEl.innerHTML = `
          <div style="margin-bottom:12px; font-weight:600; color:var(--accent);">
            Gesamt: ${totalDups} entfernte Doppelberichte
          </div>
        ` + sortedDups.map(([src, count]) => `
          <div class="modal-row">
            <span style="font-weight:500; color:var(--text);">${escapeHtml(src)}</span>
            <span class="badge" style="background:var(--accent-dim); color:var(--accent); font-weight:600;">${count} Dubletten</span>
          </div>
        `).join('');
      }
      document.getElementById('duplicate-modal').style.display = 'flex';
    }

    function closeDuplicateModal() {
      document.getElementById('duplicate-modal').style.display = 'none';
    }
"""


def render_html_dashboard(feed_health=None, feeds=None):
    now_str = datetime.datetime.now(BERLIN_TZ).strftime("%d.%m.%Y, %H:%M Uhr")
    health_text = ""
    health_json = "[]"
    if feed_health:
        total_feeds = len(feed_health)
        ok_feeds = sum(1 for h in feed_health if h["status"] == "ok" or h["code"] in (200, 304))
        failed = [h for h in feed_health if not (h["status"] == "ok" or h["code"] in (200, 304))]
        health_json = json.dumps(feed_health, ensure_ascii=False)
        if ok_feeds == total_feeds:
            health_text = f'<span style="cursor:pointer;" onclick="openHealthModal()" title="Klicken für Feed-Details">🟢 {ok_feeds}/{total_feeds} Feeds online</span>'
        else:
            failed_names = ", ".join(f["title"] for f in failed[:2])
            health_text = f'<span style="color:#eab308; cursor:pointer;" onclick="openHealthModal()" title="Klicken für Fehlerdetails: {failed_names}">🟡 {ok_feeds}/{total_feeds} Feeds ({len(failed)} gestört) ℹ️</span>'

    all_source_names = [f["title"] for f in (feeds or [])]
    all_sources_json = json.dumps(all_source_names, ensure_ascii=False)

    template = """<!DOCTYPE html>
<html lang="de" data-theme="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
  <title>News-Hub</title>
  <meta name="mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
  <meta name="theme-color" content="#121418">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap">
  <script src="https://cdnjs.cloudflare.com/ajax/libs/crypto-js/4.2.0/crypto-js.min.js"></script>
  <style>
    __SHARED_CSS__
    #auth-overlay {
      position: fixed; inset: 0; background: var(--bg);
      display: flex; align-items: center; justify-content: center; z-index: 1000;
    }
    .auth-card {
      background: var(--sidebar-bg); border: 1px solid var(--border);
      border-radius: 12px; padding: 32px 28px; width: 100%; max-width: 380px;
      text-align: center; box-shadow: 0 10px 25px rgba(0,0,0,0.1);
    }
    .auth-card h2 { font-size: 1.3rem; margin-bottom: 8px; color: var(--text-bold); }
    .auth-card p { font-size: 0.85rem; color: var(--text-muted); margin-bottom: 20px; }
    .auth-input {
      width: 100%; background: var(--card-bg); border: 1px solid var(--border);
      color: var(--text); padding: 12px 14px; border-radius: 6px; font-size: 0.95rem;
      margin-bottom: 14px; outline: none;
    }
    .auth-input:focus { border-color: var(--accent); }
    .auth-btn {
      width: 100%; background: var(--accent); color: #fff; border: none;
      padding: 12px; border-radius: 6px; font-weight: 600; cursor: pointer;
    }
    .auth-error { color: #ef4444; font-size: 0.8rem; margin-top: 10px; display: none; }
    .mark-all-btn {
      width: 100%; background: var(--border); color: var(--text); border: none;
      padding: 8px 12px; border-radius: 6px; font-size: 0.8rem; font-weight: 500;
      cursor: pointer; display: flex; align-items: center; justify-content: center;
      gap: 6px; margin-bottom: 8px;
    }
    .mark-all-btn:hover { opacity: 0.9; }
    .shortcuts-hint { font-size: 0.7rem; color: var(--text-muted); text-align: center; margin-top: 6px; }
  </style>
</head>
<body>
  <div id="auth-overlay">
    <div class="auth-card">
      <h2>🔐 Geschützter Feed Hub</h2>
      <p>Gib dein Passwort ein, um die verschlüsselten Artikel zu laden.</p>
      <input type="password" id="auth-password" class="auth-input" placeholder="Passwort eingeben..." onkeydown="if(event.key==='Enter') submitAuth()">
      <button class="auth-btn" onclick="submitAuth()">Entschlüsseln</button>
      <div id="auth-error" class="auth-error">Ungültiges Passwort!</div>
    </div>
  </div>

  __SHARED_MODALS__

  <div class="sidebar-backdrop" id="backdrop" onclick="toggleSidebar()"></div>

  <aside class="sidebar" id="sidebar">
    <div class="sidebar-header">
      <div>
        <h1>⚡ News-Hub</h1>
        <p style="font-size: 0.75rem; color: var(--text-muted); margin-top: 4px;">
          Stand: __NOW_STR__<br>
          <span style="color: var(--accent); cursor: pointer;" id="sidebar-dup-info" onclick="openDuplicateModal()" title="Klicken für Dubletten-Statistik">🧹 Duplikate bereinigt ℹ️</span>
          __HEALTH_BLOCK__
        </p>
      </div>
      <button class="close-btn" onclick="toggleSidebar()">&times;</button>
    </div>
    <ul class="source-list" id="source-list">
      <li>
        <button class="source-btn active" onclick="filterSource('all', this)">
          <span>Alle Meldungen</span>
          <span class="badge" id="total-badge">0</span>
        </button>
      </li>
    </ul>
    <div class="sidebar-footer">
      <a href="archive.html" class="archive-link-btn" style="margin-bottom:8px;">📑 Zum Archiv (24–48h)</a>
      <button class="mark-all-btn" onclick="markAllAsRead()">✓ Alle als gelesen markieren</button>
      <div class="shortcuts-hint">Tasten: <strong>J/K</strong> Nav • <strong>O</strong> Öffnen • <strong>M</strong> Gelesen • <strong>[</strong> Menü</div>
    </div>
  </aside>

  <main class="main">
    <div class="stream-header">
      <div class="header-left">
        <button class="menu-toggle" onclick="toggleSidebar()" title="Menü ein-/ausblenden (Taste: [)">☰</button>
        <h2 id="current-title">Alle Meldungen</h2>
      </div>
      <div class="header-right">
        <input type="search" class="search-input" id="search-box" placeholder="Artikel durchsuchen..." oninput="filterSearch(this.value)">
        <button class="menu-toggle" id="refresh-btn" onclick="triggerWorkflow()" title="News sofort via GitHub Action aktualisieren">🔄</button>
        <button class="theme-toggle" id="theme-btn" onclick="toggleTheme()" title="Dark/Light Mode umschalten">🌓</button>
      </div>
    </div>
    <div id="articles-container" class="cards-grid"></div>
  </main>

  <script>
    let rawEncryptedData = "";
    let globalArticles = [];
    let liveArticles = [];
    let activeArticlesCollection = [];
    let allSourceCounts = {};
    const configuredSources = __CONFIGURED_SOURCES__;
    const feedHealthData = __HEALTH_DATA__;

    __SHARED_JS__

    async function triggerWorkflow() {
      const btn = document.getElementById('refresh-btn');
      let token = localStorage.getItem('gh_dispatch_token');

      if (!token) {
        token = prompt("Bitte gib deinen GitHub Personal Access Token ein (wird nur lokal auf diesem Gerät gespeichert):");
        if (!token) return;
        localStorage.setItem('gh_dispatch_token', token.trim());
      }

      btn.style.opacity = '0.5';
      btn.style.pointerEvents = 'none';
      btn.textContent = '⏳';

      try {
        const res = await fetch('https://api.github.com/repos/schoerb/news-hub/actions/workflows/deploy.yml/dispatches', {
          method: 'POST',
          headers: { 'Accept': 'application/vnd.github+json', 'Authorization': `Bearer ${token}` },
          body: JSON.stringify({ ref: 'main' })
        });
        if (res.status === 204) {
          alert('🚀 GitHub Action gestartet! Neue Artikel sind in ca. 1-2 Minuten bereit.');
        } else if (res.status === 401 || res.status === 403) {
          localStorage.removeItem('gh_dispatch_token');
          alert('❌ Token ungültig oder abgelaufen.');
        } else {
          alert(`⚠️ GitHub API meldet Status: ${res.status}`);
        }
      } catch (err) {
        alert('Fehler beim Verbinden zur GitHub API: ' + err.message);
      } finally {
        btn.style.opacity = '1';
        btn.style.pointerEvents = 'auto';
        btn.textContent = '🔄';
      }
    }

    async function initAuth() {
      initTheme();
      try {
        const r = await fetch('data.json');
        rawEncryptedData = await r.text();
      } catch (e) {
        document.getElementById('auth-error').textContent = 'Fehler beim Laden von data.json';
        document.getElementById('auth-error').style.display = 'block';
        return;
      }

      const savedPw = localStorage.getItem('hub_key');
      if (savedPw && tryDecrypt(savedPw)) return;

      document.getElementById('auth-overlay').style.display = 'flex';
      document.getElementById('auth-password').focus();
    }

    function submitAuth() {
      const pw = document.getElementById('auth-password').value;
      if (tryDecrypt(pw)) {
        localStorage.setItem('hub_key', pw);
      } else {
        document.getElementById('auth-error').style.display = 'block';
      }
    }

    function tryDecrypt(password) {
      try {
        if (rawEncryptedData.trim().startsWith('[')) {
          globalArticles = JSON.parse(rawEncryptedData);
          onDataLoaded();
          return true;
        }
        const decrypted = CryptoJS.AES.decrypt(rawEncryptedData, password).toString(CryptoJS.enc.Utf8);
        if (!decrypted || !decrypted.startsWith('[')) return false;
        globalArticles = JSON.parse(decrypted);
        onDataLoaded();
        return true;
      } catch (e) { return false; }
    }

    function onDataLoaded() {
      document.getElementById('auth-overlay').style.display = 'none';

      const cutoffLive = new Date(Date.now() - 24 * 3600 * 1000);
      liveArticles = globalArticles.filter(a => {
        try { return new Date(a.published) >= cutoffLive; } catch(e) { return true; }
      });

      activeArticlesCollection = liveArticles;
      renderUI(liveArticles);
      initReadState();
      updateRelativeTimes();
      initSidebarState();
    }

    function renderUI(articles) {
      const container = document.getElementById('articles-container');
      const sourceList = document.getElementById('source-list');
      const totalBadge = document.getElementById('total-badge');
      totalBadge.textContent = articles.length;

      let totalDups = 0;
      allSourceCounts = {};
      articles.forEach(a => {
        totalDups += (a.other_sources || []).length;
        const s = a.source || "Unbekannt";
        allSourceCounts[s] = (allSourceCounts[s] || 0) + 1;
      });

      document.getElementById('current-title').textContent = `Alle Meldungen (${articles.length})`;
      const sidebarDupInfo = document.getElementById('sidebar-dup-info');
      if (sidebarDupInfo) sidebarDupInfo.innerHTML = `🧹 ${totalDups} Duplikate bereinigt ℹ️`;

      const knownSources = new Set([...configuredSources, ...Object.keys(allSourceCounts)]);
      const sortedSources = Array.from(knownSources).sort((a, b) => {
        const countA = allSourceCounts[a] || 0;
        const countB = allSourceCounts[b] || 0;
        return countB !== countA ? countB - countA : a.localeCompare(b);
      });

      sortedSources.forEach(sourceName => {
        const count = allSourceCounts[sourceName] || 0;
        const li = document.createElement('li');
        li.innerHTML = `
          <button class="source-btn" onclick="filterSource('${escapeHtml(sourceName)}', this)">
            <span>${escapeHtml(sourceName)}</span>
            <span class="badge">${count}</span>
          </button>`;
        sourceList.appendChild(li);
      });

      let htmlCards = "";
      articles.forEach(a => {
        const linkHash = Math.abs(hashString(a.link));
        const others = (a.other_sources && a.other_sources.length) 
          ? `<span class="feed-others">• Auch bei: ${escapeHtml(a.other_sources.join(", "))}</span>` : "";
        const img = a.image ? `<img class="feed-thumb" src="${a.image}" loading="lazy" alt="Thumbnail" onerror="this.remove()" />` : "";
        const linkedSources = [a.source, ...(a.other_sources || [])].join(";;;");

        htmlCards += `
          <article class="feed-card" data-id="${linkHash}" data-sources="${escapeHtml(linkedSources)}">
            <div class="feed-content">
              <div class="feed-meta">
                <span class="feed-source">${escapeHtml(a.source)}</span>
                <span class="feed-time" data-pub="${a.published || ''}"></span>
                ${others}
              </div>
              <a class="feed-title" href="${escapeHtml(a.link)}" target="_blank" rel="noopener" onclick="markAsRead('${linkHash}')">${escapeHtml(a.title)}</a>
              <p class="feed-summary">${a.summary}</p>
            </div>
            ${img}
          </article>`;
      });
      container.innerHTML = htmlCards;
    }

    function updateRelativeTimes() {
      document.querySelectorAll('.feed-time').forEach(el => {
        const t = el.dataset.pub;
        if (t) el.textContent = formatRelativeTime(t);
      });
    }

    function getReadArticles() {
      try { return JSON.parse(localStorage.getItem('read_news') || '[]'); } catch(e) { return []; }
    }

    function initReadState() {
      getReadArticles().forEach(id => {
        const el = document.querySelector(`.feed-card[data-id="${id}"]`);
        if (el) el.classList.add('read');
      });
    }

    function markAsRead(id) {
      let list = getReadArticles();
      if (!list.includes(id)) {
        list.push(id);
        localStorage.setItem('read_news', JSON.stringify(list));
      }
      const el = document.querySelector(`.feed-card[data-id="${id}"]`);
      if (el) el.classList.add('read');
    }

    function toggleRead(id) {
      let list = getReadArticles();
      const el = document.querySelector(`.feed-card[data-id="${id}"]`);
      if (list.includes(id)) {
        list = list.filter(x => x !== id);
        if (el) el.classList.remove('read');
      } else {
        list.push(id);
        if (el) el.classList.add('read');
      }
      localStorage.setItem('read_news', JSON.stringify(list));
    }

    function markAllAsRead() {
      const cards = document.querySelectorAll('.feed-card');
      let list = getReadArticles();
      cards.forEach(c => {
        c.classList.add('read');
        const id = c.dataset.id;
        if (!list.includes(id)) list.push(id);
      });
      localStorage.setItem('read_news', JSON.stringify(list));
    }

    let activeSource = 'all';
    function filterSource(source, btn) {
      activeSource = source;
      document.querySelectorAll('.source-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');

      document.getElementById('current-title').textContent = (source === 'all')
        ? `Alle Meldungen (${liveArticles.length})`
        : `${source} (${allSourceCounts[source] || 0})`;

      applyCombinedFilters();
      if (window.innerWidth <= 768) toggleSidebar();
    }

    let searchQuery = '';
    function filterSearch(q) {
      searchQuery = q.toLowerCase().trim();
      applyCombinedFilters();
    }

    function applyCombinedFilters() {
      document.querySelectorAll('.feed-card').forEach(card => {
        const cardSources = (card.dataset.sources || "").split(";;;");
        const matchesSource = (activeSource === 'all' || cardSources.includes(activeSource));
        const matchesSearch = !searchQuery || card.textContent.toLowerCase().includes(searchQuery);
        card.style.display = (matchesSource && matchesSearch) ? '' : 'none';
      });
    }

    let selectedIndex = -1;
    function getVisibleCards() {
      return Array.from(document.querySelectorAll('.feed-card')).filter(c => c.style.display !== 'none');
    }

    function selectCard(idx) {
      const cards = getVisibleCards();
      if (!cards.length) return;
      cards.forEach(c => c.classList.remove('selected'));
      selectedIndex = Math.max(0, Math.min(idx, cards.length - 1));
      const target = cards[selectedIndex];
      target.classList.add('selected');
      target.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }

    document.addEventListener('keydown', (e) => {
      if (document.activeElement === document.getElementById('search-box')) {
        if (e.key === 'Escape') document.getElementById('search-box').blur();
        return;
      }
      const visible = getVisibleCards();
      if (e.key === '[') { e.preventDefault(); toggleSidebar(); return; }
      if (e.key === 'Escape') { closeHealthModal(); closeDuplicateModal(); }
      if (!visible.length) return;

      if (e.key === 'j' || e.key === 'ArrowDown') { e.preventDefault(); selectCard(selectedIndex + 1); }
      else if (e.key === 'k' || e.key === 'ArrowUp') { e.preventDefault(); selectCard(selectedIndex - 1); }
      else if (e.key === 'o' || e.key === 'Enter') {
        if (selectedIndex >= 0 && selectedIndex < visible.length) {
          const link = visible[selectedIndex].querySelector('.feed-title');
          if (link) { markAsRead(visible[selectedIndex].dataset.id); window.open(link.href, '_blank'); }
        }
      } else if (e.key === 'm') {
        if (selectedIndex >= 0 && selectedIndex < visible.length) toggleRead(visible[selectedIndex].dataset.id);
      } else if (e.key === '/') {
        e.preventDefault();
        document.getElementById('search-box').focus();
      }
    });

    document.addEventListener('DOMContentLoaded', initAuth);
  </script>
</body>
</html>
"""
    health_replacement = f"<br>{health_text}" if health_text else ""
    return template.replace("__SHARED_CSS__", SHARED_CSS)\
                   .replace("__SHARED_MODALS__", SHARED_MODALS)\
                   .replace("__SHARED_JS__", SHARED_JS)\
                   .replace("__NOW_STR__", now_str)\
                   .replace("__HEALTH_BLOCK__", health_replacement)\
                   .replace("__HEALTH_DATA__", health_json)\
                   .replace("__CONFIGURED_SOURCES__", all_sources_json)


def render_archive_html(feed_health=None, feeds=None):
    now_str = datetime.datetime.now(BERLIN_TZ).strftime("%d.%m.%Y, %H:%M Uhr")
    health_text = ""
    health_json = "[]"
    if feed_health:
        total_feeds = len(feed_health)
        ok_feeds = sum(1 for h in feed_health if h["status"] == "ok" or h["code"] in (200, 304))
        failed = [h for h in feed_health if not (h["status"] == "ok" or h["code"] in (200, 304))]
        health_json = json.dumps(feed_health, ensure_ascii=False)
        if ok_feeds == total_feeds:
            health_text = f'<span style="cursor:pointer;" onclick="openHealthModal()" title="Klicken für Feed-Details">🟢 {ok_feeds}/{total_feeds} Feeds online</span>'
        else:
            failed_names = ", ".join(f["title"] for f in failed[:2])
            health_text = f'<span style="color:#eab308; cursor:pointer;" onclick="openHealthModal()" title="Klicken für Fehlerdetails: {failed_names}">🟡 {ok_feeds}/{total_feeds} Feeds ({len(failed)} gestört) ℹ️</span>'

    all_source_names = [f["title"] for f in (feeds or [])]
    all_sources_json = json.dumps(all_source_names, ensure_ascii=False)

    template = """<!DOCTYPE html>
<html lang="de" data-theme="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
  <title>Archiv</title>
  <meta name="mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
  <meta name="theme-color" content="#121418">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap">
  <script src="https://cdnjs.cloudflare.com/ajax/libs/crypto-js/4.2.0/crypto-js.min.js"></script>
  <style>
    __SHARED_CSS__
  </style>
</head>
<body>
  __SHARED_MODALS__

  <div class="sidebar-backdrop" id="backdrop" onclick="toggleSidebar()"></div>

  <aside class="sidebar" id="sidebar">
    <div class="sidebar-header">
      <div>
        <h1>⚡ Archiv (24–48h)</h1>
        <p style="font-size: 0.75rem; color: var(--text-muted); margin-top: 4px;">
          Stand: __NOW_STR__<br>
          <span style="color: var(--accent); cursor: pointer;" id="sidebar-dup-info" onclick="openDuplicateModal()" title="Klicken für Dubletten-Statistik">🧹 Duplikate bereinigt ℹ️</span>
          __HEALTH_BLOCK__
        </p>
      </div>
      <button class="close-btn" onclick="toggleSidebar()">&times;</button>
    </div>
    <ul class="source-list" id="source-list">
      <li>
        <button class="source-btn active" onclick="filterSource('all', this)">
          <span>Alle Meldungen</span>
          <span class="badge" id="total-badge">0</span>
        </button>
      </li>
    </ul>
    <div class="sidebar-footer">
      <a href="index.html" class="archive-link-btn">← Zurück zum Live-Feed</a>
    </div>
  </aside>

  <main class="main">
    <div class="stream-header">
      <div class="header-left">
        <button class="menu-toggle" onclick="toggleSidebar()" title="Menü ein-/ausblenden (Taste: [)">☰</button>
        <h2 id="current-title">Archiv</h2>
      </div>
      <div class="header-right">
        <input type="search" class="search-input" id="search-box" placeholder="Archiv durchsuchen..." oninput="filterSearch(this.value)">
        <button class="theme-toggle" id="theme-btn" onclick="toggleTheme()" title="Dark/Light Mode umschalten">🌓</button>
      </div>
    </div>
    <div id="archive-container" class="cards-grid"></div>
  </main>

  <script>
    let archiveArticles = [];
    let activeArticlesCollection = [];
    let allSourceCounts = {};
    const configuredSources = __CONFIGURED_SOURCES__;
    const feedHealthData = __HEALTH_DATA__;

    __SHARED_JS__

    async function loadArchive() {
      initTheme();
      initSidebarState();

      let articles = [];
      try {
        const r = await fetch('data.json');
        const enc = await r.text();
        const pw = localStorage.getItem('hub_key');

        if (enc.trim().startsWith('[')) {
          articles = JSON.parse(enc);
        } else if (pw) {
          const decrypted = CryptoJS.AES.decrypt(enc, pw).toString(CryptoJS.enc.Utf8);
          articles = JSON.parse(decrypted);
        }
      } catch(e) {}

      if (!articles || !articles.length) {
        document.getElementById('current-title').textContent = 'Archiv (0)';
        document.getElementById('archive-container').innerHTML = 
          '<p style="color:var(--text-muted); padding:20px 0;">Keine Daten geladen oder Passwort fehlt. Bitte erst im Live-Feed anmelden.</p>';
        return;
      }

      const now = Date.now();
      const cutoffRecent = new Date(now - 24 * 3600 * 1000);
      const cutoffOld = new Date(now - 48 * 3600 * 1000);

      archiveArticles = articles.filter(a => {
        try {
          const pub = new Date(a.published);
          return pub < cutoffRecent && pub >= cutoffOld;
        } catch(e) { return false; }
      });

      activeArticlesCollection = archiveArticles;
      renderArchiveUI();
    }

    function renderArchiveUI() {
      const container = document.getElementById('archive-container');
      const sourceList = document.getElementById('source-list');
      const totalBadge = document.getElementById('total-badge');
      totalBadge.textContent = archiveArticles.length;

      let totalDups = 0;
      allSourceCounts = {};
      archiveArticles.forEach(a => {
        totalDups += (a.other_sources || []).length;
        const s = a.source || "Unbekannt";
        allSourceCounts[s] = (allSourceCounts[s] || 0) + 1;
      });

      document.getElementById('current-title').textContent = `Archiv (${archiveArticles.length})`;
      const sidebarDupInfo = document.getElementById('sidebar-dup-info');
      if (sidebarDupInfo) sidebarDupInfo.innerHTML = `🧹 ${totalDups} Duplikate bereinigt ℹ️`;

      const knownSources = new Set([...configuredSources, ...Object.keys(allSourceCounts)]);
      const sortedSources = Array.from(knownSources).sort((a, b) => {
        const countA = allSourceCounts[a] || 0;
        const countB = allSourceCounts[b] || 0;
        return countB !== countA ? countB - countA : a.localeCompare(b);
      });

      sortedSources.forEach(sourceName => {
        const count = allSourceCounts[sourceName] || 0;
        const li = document.createElement('li');
        li.innerHTML = `
          <button class="source-btn" onclick="filterSource('${escapeHtml(sourceName)}', this)">
            <span>${escapeHtml(sourceName)}</span>
            <span class="badge">${count}</span>
          </button>`;
        sourceList.appendChild(li);
      });

      let htmlCards = "";
      archiveArticles.forEach(a => {
        const linkHash = Math.abs(hashString(a.link));
        const others = (a.other_sources && a.other_sources.length) 
          ? `<span class="feed-others">• Auch bei: ${escapeHtml(a.other_sources.join(", "))}</span>` : "";
        const img = a.image ? `<img class="feed-thumb" src="${a.image}" loading="lazy" alt="Thumbnail" onerror="this.remove()" />` : "";
        const linkedSources = [a.source, ...(a.other_sources || [])].join(";;;");
        const timeAgo = formatRelativeTime(a.published);

        htmlCards += `
          <article class="feed-card" data-id="${linkHash}" data-sources="${escapeHtml(linkedSources)}">
            <div class="feed-content">
              <div class="feed-meta">
                <span class="feed-source">${escapeHtml(a.source)}</span>
                <span class="feed-time">${timeAgo}</span>
                ${others}
              </div>
              <a class="feed-title" href="${escapeHtml(a.link)}" target="_blank" rel="noopener">${escapeHtml(a.title)}</a>
              <p class="feed-summary">${a.summary}</p>
            </div>
            ${img}
          </article>`;
      });
      container.innerHTML = htmlCards;
    }

    let activeSource = 'all';
    function filterSource(source, btn) {
      activeSource = source;
      document.querySelectorAll('.source-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');

      document.getElementById('current-title').textContent = (source === 'all')
        ? `Archiv (${archiveArticles.length})`
        : `${source} (${allSourceCounts[source] || 0})`;

      applyCombinedFilters();
      if (window.innerWidth <= 768) toggleSidebar();
    }

    let searchQuery = '';
    function filterSearch(q) {
      searchQuery = q.toLowerCase().trim();
      applyCombinedFilters();
    }

    function applyCombinedFilters() {
      document.querySelectorAll('.feed-card').forEach(card => {
        const cardSources = (card.dataset.sources || "").split(";;;");
        const matchesSource = (activeSource === 'all' || cardSources.includes(activeSource));
        const matchesSearch = !searchQuery || card.textContent.toLowerCase().includes(searchQuery);
        card.style.display = (matchesSource && matchesSearch) ? '' : 'none';
      });
    }

    document.addEventListener('keydown', (e) => {
      if (document.activeElement === document.getElementById('search-box')) {
        if (e.key === 'Escape') document.getElementById('search-box').blur();
        return;
      }
      if (e.key === '[') { e.preventDefault(); toggleSidebar(); }
      else if (e.key === 'Escape') { closeHealthModal(); closeDuplicateModal(); }
      else if (e.key === '/') { e.preventDefault(); document.getElementById('search-box').focus(); }
    });

    document.addEventListener('DOMContentLoaded', loadArchive);
  </script>
</body>
</html>
"""
    health_replacement = f"<br>{health_text}" if health_text else ""
    return template.replace("__SHARED_CSS__", SHARED_CSS)\
                   .replace("__SHARED_MODALS__", SHARED_MODALS)\
                   .replace("__SHARED_JS__", SHARED_JS)\
                   .replace("__NOW_STR__", now_str)\
                   .replace("__HEALTH_BLOCK__", health_replacement)\
                   .replace("__HEALTH_DATA__", health_json)\
                   .replace("__CONFIGURED_SOURCES__", all_sources_json)


if __name__ == "__main__":
    os.makedirs("public", exist_ok=True)
    page_password = os.environ.get("PAGE_PASSWORD", "")

    # 1. State und Cache laden
    cached_articles, cache_meta = load_cached_state()
    cached_articles = expire_old_articles(cached_articles)
    feeds = parse_opml()

    # 2. Feeds abrufen (Thread-Safe)
    raw_feed_items, updated_cache_meta, feed_health = fetch_all_feeds(feeds, cache_meta)
    with open("cache_meta.json", "w", encoding="utf-8") as f:
        json.dump(updated_cache_meta, f, separators=(',', ':'))

    # 3. Cross-Check vor Gemini
    truly_new_items = []
    for raw in raw_feed_items:
        if any(raw["link"] == c["link"] for c in cached_articles):
            continue

        matched_cached = next((c for c in cached_articles if is_duplicate(raw["title"], c["title"])), None)
        if matched_cached:
            others = matched_cached.setdefault("other_sources", [])
            if raw["source"] != matched_cached["source"] and raw["source"] not in others:
                others.append(raw["source"])
        else:
            truly_new_items.append(raw)

    # 4. Batch-Deduplizierung
    bundled_new = consolidate_articles(truly_new_items)

    # 5. Echte Unikate an Gemini senden (Titel-Optimierung & Zusammenfassung)
    if bundled_new:
        processed_new = summarize_delta_with_gemini(bundled_new)
        combined_articles = processed_new + cached_articles
    else:
        combined_articles = cached_articles

    # 6. Globaler Bereinigungslauf
    cleaned_articles = consolidate_articles(combined_articles)

    # 7. Chronologisch sortieren (über vorkalkulierten _ts-Wert) & Payload abspecken
    final_articles = sorted(cleaned_articles, key=lambda a: a.get("_ts", 0), reverse=True)

    frontend_articles = [
        {
            "title": a["title"],
            "link": a["link"],
            "source": a["source"],
            "other_sources": a.get("other_sources", []),
            "summary": a["summary"],
            "image": a.get("image"),
            "published": a.get("published"),
        }
        for a in final_articles
    ]

    has_changes = bool(bundled_new) or (len(cached_articles) != len(cleaned_articles))

    if "GITHUB_OUTPUT" in os.environ:
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as gh_out:
            gh_out.write(f"deploy={'true' if has_changes else 'false'}\n")

    # 8. State kompakt serialisieren und verschlüsseln
    articles_json = json.dumps(frontend_articles, ensure_ascii=False, separators=(',', ':'))
    if page_password:
        encrypted_payload = encrypt_payload(articles_json, page_password)
        with open("public/data.json", "w", encoding="utf-8") as f:
            f.write(encrypted_payload)
    else:
        with open("public/data.json", "w", encoding="utf-8") as f:
            f.write(articles_json)

    # 9. HTML-Seiten generieren
    html_page = render_html_dashboard(feed_health, feeds)
    with open("public/index.html", "w", encoding="utf-8") as f:
        f.write(html_page)

    archive_page = render_archive_html(feed_health, feeds)
    with open("public/archive.html", "w", encoding="utf-8") as f:
        f.write(archive_page)
