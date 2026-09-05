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
REMOTE_DATA_URL = "https://schoerb.github.io/news-hub/data.json"
BERLIN_TZ = zoneinfo.ZoneInfo("Europe/Berlin")

DEDUP_RATIO_THRESHOLD = float(os.environ.get("DEDUP_RATIO", "0.78"))
DEDUP_OVERLAP_THRESHOLD = float(os.environ.get("DEDUP_OVERLAP", "0.65"))

STOPWORDS = {
    "im", "in", "der", "die", "das", "den", "dem", "des", "für", "von", "mit", "ab", "sofort",
    "neu", "neue", "neues", "neuen", "neuer", "update", "bringt", "startet", "erhält", "offiziell",
    "jetzt", "nach", "zum", "zur", "wie", "auf", "ein", "eine", "einen", "einem", "einer",
    "als", "sich", "nicht", "auch", "über", "test", "getestet", "bericht", "schlägt", "überzeugt",
    "zeigt", "soll", "gibt", "erstes", "erste", "erster", "download", "verfügbar", "rollt", "aus",
    "wird", "kann", "haben", "mehr", "dieses", "dieser", "diese", "the", "a", "an", "and", "or",
    "in", "on", "at", "to", "for", "of", "with", "by", "from", "is", "are", "was", "were", "be",
    "has", "have", "had", "will", "can", "how", "what", "new", "gets", "brings", "adds", "rolls",
    "out", "now", "available", "first", "look", "review"
}

# --- Globaler Connection Pool ---
session = requests.Session()
adapter = HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=Retry(total=2, backoff_factor=0.3))
session.mount("https://", adapter)
session.mount("http://", adapter)
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "application/rss+xml, application/xml, text/xml, text/html, application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
})

# --- Krypto & Hash-Helfer ---
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
        key, iv = openssl_kdf(password.encode("utf-8"), salt)
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        decryptor = cipher.decryptor()
        padded_data = decryptor.update(raw[16:]) + decryptor.finalize()
        return padded_data[:-padded_data[-1]].decode("utf-8")
    except Exception:
        return ""


def hash_feed_url(url: str) -> str:
    salt = os.environ.get("PAGE_PASSWORD", "static_news_salt")
    return hashlib.sha256((url + salt).encode("utf-8")).hexdigest()[:16]


def get_private_priorities() -> dict:
    raw = os.environ.get("FEED_PRIORITIES", "").strip()
    return json.loads(raw) if raw else {}


# --- Pydantic Schemas ---
class DeltaItem(BaseModel):
    id: int = Field(description="Index des Artikels aus dem Batch")
    german_title: str = Field(description="Zwingend auf DEUTSCH. Englische Titel vollständig und sinngemäß ins Deutsche übersetzen. Kein Clickbait! Konkretes Modell/Zahl/Fehler nennen.")
    summary: str = Field(description="Genau 1 prägnanter deutscher Satz. Schlüsselbegriffe mit **fett** hervorheben.")
    use_image: bool = Field(default=False, description="True NUR wenn das Bild ein konkretes Gerät, UI-Element oder Chart zeigt.")


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
    raw_opml = os.environ.get("FEEDS_OPML", "").strip()
    priorities = get_private_priorities()
    try:
        tree = ET.fromstring(raw_opml) if raw_opml else (ET.parse("feeds.opml").getroot() if os.path.exists("feeds.opml") else None)
    except Exception:
        return []
    if tree is None:
        return []

    feeds = []
    for node in tree.findall(".//outline[@xmlUrl]"):
        name = (node.get("text") or node.get("title") or "Feed").strip()
        url = node.get("xmlUrl", "").strip()
        if url:
            prio = int(node.get("priority")) if node.get("priority", "").isdigit() else priorities.get(name, DEFAULT_PRIO)
            feeds.append({"title": name, "url": url, "priority": prio})
    return feeds


def extract_image(entry):
    for k in ("media_content", "media_thumbnail"):
        if entry.get(k):
            u = entry[k][0].get("url")
            if u and not any(b in u.lower() for b in ["favicon", "avatar", "logo", "pixel", "tracking", "1x1", "icon"]):
                return u
    for enc in entry.get("enclosures", []):
        if enc.get("type", "").startswith("image/"):
            u = enc.get("href")
            if u:
                return u
    content = entry.get("summary", "") + (entry.content[0].get("value", "") if "content" in entry and entry.content else "")
    m = re.search(r'<img[^>]+src=["\']?([^\s"\'<>]+\.(?:jpg|jpeg|png|webp))', content, re.I)
    return m.group(1) if m and not any(b in m.group(1).lower() for b in ["favicon", "pixel", "tracking", "1x1"]) else None


def parse_timestamp(iso_or_obj) -> int:
    if isinstance(iso_or_obj, (int, float)):
        return int(iso_or_obj)
    if not iso_or_obj:
        return 0
    try:
        return int(datetime.datetime.fromisoformat(str(iso_or_obj).replace("Z", "+00:00")).timestamp())
    except Exception:
        return 0


def load_cached_state():
    articles, cache_meta = [], {}
    pw = os.environ.get("PAGE_PASSWORD", "")
    force = os.environ.get("FORCE_REFRESH", "").lower() in ("true", "1")

    source_path = "public/data.json"
    content = ""
    if os.path.exists(source_path):
        try:
            with open(source_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
        except Exception:
            pass
    elif REMOTE_DATA_URL and not force:
        try:
            r = session.get(REMOTE_DATA_URL, timeout=4)
            if r.ok:
                content = r.text.strip()
        except Exception:
            pass

    if content and content != "[]":
        try:
            articles = json.loads(decrypt_payload(content, pw) if pw else content)
        except Exception:
            pass

    if os.path.exists("cache_meta.json") and not force:
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
    new_cache_meta, feed_health = dict(cache_meta), []

    def _fetch(f):
        url, key = f["url"], hash_feed_url(f["url"])
        headers = {}
        if key in cache_meta:
            if "etag" in cache_meta[key]:
                headers["If-None-Match"] = cache_meta[key]["etag"]
            if "modified" in cache_meta[key]:
                headers["If-Modified-Since"] = cache_meta[key]["modified"]

        try:
            parsed_domain = urllib.parse.urlsplit(url)
            if parsed_domain.scheme and parsed_domain.netloc:
                headers["Referer"] = f"{parsed_domain.scheme}://{parsed_domain.netloc}/"

            r = session.get(url, headers=headers, timeout=8)
            if r.status_code == 304:
                return [], {"title": f["title"], "status": "ok", "code": 304}
            if not r.ok:
                return [], {"title": f["title"], "status": "error", "code": r.status_code}

            meta = {}
            if "etag" in r.headers:
                meta["etag"] = r.headers["etag"]
            if "last-modified" in r.headers:
                meta["modified"] = r.headers["last-modified"]
            if meta:
                new_cache_meta[key] = meta

            parsed = feedparser.parse(r.content)
            if parsed.bozo and not parsed.entries:
                return [], {"title": f["title"], "status": "parse_error", "code": r.status_code}

            items = []
            for e in parsed.entries[:15]:
                dt = None
                for attr in ("published_parsed", "updated_parsed"):
                    if getattr(e, attr, None):
                        dt = datetime.datetime(*getattr(e, attr)[:6], tzinfo=datetime.timezone.utc)
                        break
                entry_ts = int(dt.timestamp()) if dt else int(now.timestamp())
                pub_iso = dt.isoformat() if dt else now.isoformat()

                if entry_ts > cutoff_ts:
                    summary = " ".join(re.sub(r"<[^>]+>", " ", e.get("summary", "")).split())[:350]
                    items.append({
                        "title": e.title.strip(),
                        "summary": summary,
                        "link": clean_url(e.link.strip()),
                        "image": extract_image(e),
                        "source": f["title"].strip(),
                        "priority": f["priority"],
                        "published": pub_iso,
                        "_ts": entry_ts,
                    })
            return items, {"title": f["title"], "status": "ok", "code": r.status_code}
        except Exception:
            return [], {"title": f["title"], "status": "exception", "code": "timeout/conn"}

    all_items = []
    with ThreadPoolExecutor(max_workers=12) as ex:
        for items, health in ex.map(_fetch, feeds):
            all_items.extend(items)
            feed_health.append(health)

    return all_items, new_cache_meta, feed_health


# --- Deduplizierung ---
def clean_stem(word: str) -> str:
    w = word.lower().strip()
    for end in ("s", "n", "en", "er", "es", "e"):
        if w.endswith(end) and len(w) > 4:
            return w[:-len(end)]
    return w


def extract_keywords(title: str) -> tuple[set, set]:
    words = re.sub(r"[^\w\s\.]", " ", title.lower()).split()
    numbers = {w for w in words if any(c.isdigit() for c in w) and len(w) >= 2}
    keywords = {clean_stem(w) for w in words if w not in STOPWORDS and len(w) > 2 and w not in numbers}
    return keywords, numbers


def is_duplicate(title_a: str, title_b: str, memo_a=None, memo_b=None) -> bool:
    kw_a, num_a = memo_a or extract_keywords(title_a)
    kw_b, num_b = memo_b or extract_keywords(title_b)

    common_nums = num_a & num_b
    common_kws = kw_a & kw_b
    if common_nums and len(common_kws) >= 2:
        return True

    sub_matches = {wa[:5] for wa in kw_a for wb in kw_b if len(wa) >= 5 and len(wb) >= 5 and wa[:5] == wb[:5]}
    min_len = min(len(kw_a), len(kw_b))
    if min_len >= 3 and ((len(common_kws | sub_matches)) / min_len) >= DEDUP_OVERLAP_THRESHOLD:
        return True

    if not common_kws and not sub_matches:
        return False
    if min(len(title_a), len(title_b)) / max(len(title_a), len(title_b)) < 0.65:
        return False

    clean_a = re.sub(r"[^\w\s]", "", title_a.lower())
    clean_b = re.sub(r"[^\w\s]", "", title_b.lower())
    return SequenceMatcher(None, clean_a, clean_b).ratio() >= DEDUP_RATIO_THRESHOLD


def consolidate_articles(articles: list[dict]) -> list[dict]:
    sorted_arts = sorted(articles, key=lambda x: x.get("priority", DEFAULT_PRIO), reverse=True)
    unique_list, cached_features = [], []

    for item in sorted_arts:
        feat = extract_keywords(item["title"])
        match = next((existing for idx, existing in enumerate(unique_list)
                      if is_duplicate(item["title"], existing["title"], feat, cached_features[idx])), None)
        if match:
            src = item.get("source")
            others = match.setdefault("other_sources", [])
            if src and src != match.get("source") and src not in others:
                others.append(src)
            for osrc in item.get("other_sources", []):
                if osrc != match.get("source") and osrc not in others:
                    others.append(osrc)

            merged = match.setdefault("merged_details", [])
            merged.append({
                "source": src or "Unbekannt",
                "title": item.get("title", ""),
                "link": item.get("link", ""),
                "matched_with": match.get("title", "")
            })
            merged.extend(item.get("merged_details", []))
        else:
            item_copy = dict(item)
            item_copy.setdefault("other_sources", [])
            item_copy.setdefault("merged_details", [])
            unique_list.append(item_copy)
            cached_features.append(feat)

    return unique_list


def summarize_chunk_with_gemini(client, chunk_items, max_retries=3):
    payload = [{
        "id": idx,
        "original_title": a["title"],
        "source": a["source"],
        "raw_text": a["summary"],
        "has_image": bool(a.get("image")),
    } for idx, a in enumerate(chunk_items)]

    prompt = f"""
Du bist Chefredakteur eines deutschsprachigen Tech-Nachrichtenmagazins.
1. 'german_title': JEDER englische Titel MUSS vollständig ins DEUTSCHE übersetzt werden. Kein Clickbait! Konkretes Modell/Zahl/Fehler nennen.
2. 'summary': Genau 1 prägnanter deutscher Satz. 2-3 zentrale Schlüsselwörter mit **fett** hervorheben.
3. 'use_image': True NUR wenn das Bild ein konkretes Gerät, Screenshot oder Chart zeigt.

Artikel:
{json.dumps(payload, ensure_ascii=False)}
"""
    models = ["gemini-3.5-flash-lite", "gemini-3.6-flash"]
    for attempt in range(max_retries):
        selected_model = models[min(attempt, len(models) - 1)]
        try:
            res = client.models.generate_content(
                model=selected_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    response_mime_type="application/json",
                    response_schema=DeltaBatchResponse,
                ),
            )
            parsed = DeltaBatchResponse.model_validate_json(res.text)
            processed = []
            for item in parsed.items:
                if 0 <= item.id < len(chunk_items):
                    orig = chunk_items[item.id]
                    clean_sum = html.escape(item.summary.strip())
                    clean_sum = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", clean_sum)
                    processed.append({
                        "title": html.escape(item.german_title.strip()) or orig["title"],
                        "link": orig["link"],
                        "source": orig["source"],
                        "other_sources": orig.get("other_sources", []),
                        "merged_details": orig.get("merged_details", []),
                        "summary": clean_sum,
                        "image": orig["image"] if item.use_image else None,
                        "published": orig["published"],
                        "_ts": orig.get("_ts", 0),
                    })
            return processed
        except Exception as err:
            wait_time = 22 + (attempt * 6) if ("429" in str(err) or "RESOURCE_EXHAUSTED" in str(err)) else ((2 ** attempt) * 2)
            time.sleep(wait_time)

    return [{
        "title": o["title"], "link": o["link"], "source": o["source"],
        "other_sources": o.get("other_sources", []), "merged_details": o.get("merged_details", []),
        "summary": html.escape(o["summary"]), "image": o["image"], "published": o["published"], "_ts": o.get("_ts", 0)
    } for o in chunk_items]


def summarize_delta_with_gemini(new_items):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not new_items or not api_key:
        return []

    client = genai.Client(api_key=api_key)
    chunks = [new_items[i:i + 35] for i in range(0, len(new_items), 35)]
    all_processed = []
    with ThreadPoolExecutor(max_workers=2) as ex:
        for res in ex.map(lambda c: summarize_chunk_with_gemini(client, c), chunks):
            all_processed.extend(res)
    return all_processed


def expire_old_articles(articles):
    cutoff_ts = int((datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=MAX_RETENTION_HOURS)).timestamp())
    return [a for a in articles if a.get("_ts", 0) > cutoff_ts]


# --- Unified HTML Generator ---
PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="de" data-theme="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
  <title>__PAGE_TITLE__</title>
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap">
  <script src="https://cdnjs.cloudflare.com/ajax/libs/crypto-js/4.2.0/crypto-js.min.js"></script>
  <style>
    :root {
      --bg: #121418; --sidebar-bg: #181b20; --card-bg: #1e2229; --card-hover: #262b34;
      --border: #2e3440; --text: #e2e8f0; --text-muted: #94a3b8; --text-bold: #f1f5f9;
      --accent: #2ecc71; --accent-dim: rgba(46, 204, 113, 0.15); --link: #60a5fa; --focus-ring: #3b82f6;
    }
    [data-theme="light"] {
      --bg: #f8fafc; --sidebar-bg: #ffffff; --card-bg: #ffffff; --card-hover: #f1f5f9;
      --border: #e2e8f0; --text: #1e293b; --text-muted: #64748b; --text-bold: #0f172a;
      --accent: #16a34a; --accent-dim: rgba(22, 163, 74, 0.12); --link: #2563eb; --focus-ring: #2563eb;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Inter', -apple-system, sans-serif; background-color: var(--bg);
      color: var(--text); display: flex; height: 100vh; overflow: hidden;
    }
    .modal-overlay {
      display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.75);
      backdrop-filter: blur(2px); z-index: 1100; align-items: center; justify-content: center; padding: 16px;
    }
    .modal-card {
      background: var(--sidebar-bg); border: 1px solid var(--border); border-radius: 12px;
      padding: 24px; width: 100%; max-width: 520px; max-height: 80vh; display: flex; flex-direction: column;
    }
    .modal-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; border-bottom: 1px solid var(--border); padding-bottom: 10px; }
    .modal-body { overflow-y: auto; flex-grow: 1; font-size: 0.88rem; }
    .modal-row { display: flex; justify-content: space-between; align-items: center; padding: 10px 0; border-bottom: 1px solid var(--border); gap: 12px; }
    .modal-close-btn { width: 100%; background: var(--accent); color: #fff; border: none; padding: 12px; border-radius: 6px; font-weight: 600; cursor: pointer; margin-top: 16px; }
    
    .sidebar-backdrop { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.65); z-index: 90; }
    .sidebar {
      width: 290px; background: var(--sidebar-bg); border-right: 1px solid var(--border);
      display: flex; flex-direction: column; flex-shrink: 0; z-index: 100;
      transition: width 0.25s cubic-bezier(0.4, 0, 0.2, 1), transform 0.25s cubic-bezier(0.4, 0, 0.2, 1);
      overflow: hidden; white-space: nowrap;
    }
    .sidebar.collapsed {
      width: 0 !important; border-right: none !important; visibility: hidden;
    }
    .sidebar-header { padding: 20px; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; min-width: 290px; }
    .close-btn { background: none; border: none; color: var(--text-muted); font-size: 1.4rem; cursor: pointer; }
    .source-list { list-style: none; padding: 12px; overflow-y: auto; flex-grow: 1; min-width: 290px; }
    .source-btn {
      width: 100%; text-align: left; padding: 10px 14px; margin-bottom: 4px; border-radius: 6px;
      background: transparent; border: none; color: var(--text-muted); font-size: 0.85rem; font-weight: 500;
      cursor: pointer; display: flex; justify-content: space-between; align-items: center;
    }
    .source-btn:hover, .source-btn.active { background: var(--accent-dim); color: var(--accent); font-weight: 600; }
    .badge { background: var(--border); padding: 2px 8px; border-radius: 12px; font-size: 0.7rem; color: var(--text); }
    .source-btn.active .badge { background: var(--accent); color: #fff; }
    .sidebar-footer { padding: 16px; border-top: 1px solid var(--border); min-width: 290px; }
    .nav-link-btn { display: block; text-align: center; color: var(--accent); text-decoration: none; font-size: 0.82rem; font-weight: 600; padding: 8px; border-radius: 6px; background: var(--accent-dim); margin-bottom: 8px; }
    .mark-all-btn { width: 100%; background: var(--border); color: var(--text); border: none; padding: 8px 12px; border-radius: 6px; font-size: 0.8rem; cursor: pointer; }

    .main { flex-grow: 1; overflow-y: auto; padding: 0; position: relative; }
    .stream-header {
      position: sticky; top: 0; z-index: 50; background: rgba(18, 20, 24, 0.55);
      backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px); border-bottom: 1px solid var(--border);
      padding: 14px 36px; display: flex; justify-content: space-between; align-items: center; gap: 16px;
      transition: transform 0.28s cubic-bezier(0.4, 0, 0.2, 1);
    }
    .stream-header.header-hidden { transform: translateY(-100%); }
    [data-theme="light"] .stream-header { background: rgba(248, 250, 252, 0.65); }
    .header-left { display: flex; align-items: center; gap: 14px; min-width: 0; }
    .header-right { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
    .header-title-group { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
    .stream-header h2 { font-size: 1.3rem; font-weight: 700; color: var(--text-bold); white-space: nowrap; line-height: 1.25; }
    .header-meta-inline { display: flex; align-items: center; gap: 8px; font-size: 0.85rem; color: var(--text-muted); white-space: nowrap; }
    .meta-sep { color: var(--border); }
    .meta-clickable { color: var(--accent); cursor: pointer; }
    .meta-clickable:hover { text-decoration: underline; }

    .theme-toggle, .menu-toggle {
      background: var(--card-bg); border: 1px solid var(--border); border-radius: 6px;
      color: var(--text); font-size: 1.1rem; padding: 6px 10px; cursor: pointer;
    }
    .search-input {
      background: var(--card-bg); border: 1px solid var(--border); color: var(--text);
      padding: 8px 14px; border-radius: 6px; font-size: 0.85rem; outline: none; width: 240px;
    }

    .cards-grid { padding: 20px 36px 60px; display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 18px; }
    .feed-card {
      background: var(--card-bg); border: 1px solid var(--border); border-radius: 10px; padding: 18px;
      display: flex; flex-direction: column; justify-content: space-between; transition: transform 0.15s, opacity 0.25s;
    }
    .feed-card:hover { transform: translateY(-2px); background: var(--card-hover); }
    .feed-card.selected { border-color: var(--focus-ring); box-shadow: 0 0 0 2px var(--focus-ring); }
    .feed-card.seen { opacity: 0.72; }
    .feed-card.read { opacity: 0.35 !important; }
    .feed-card.read .feed-title { color: var(--text-muted) !important; }
    .feed-meta { display: flex; align-items: center; gap: 6px; font-size: 0.75rem; margin-bottom: 8px; flex-wrap: wrap; }
    .feed-source { color: var(--accent); font-weight: 600; }
    .feed-time, .feed-others { color: var(--text-muted); font-size: 0.72rem; }
    .feed-title { font-size: 1.05rem; font-weight: 600; color: var(--text-bold); text-decoration: none; line-height: 1.4; margin-bottom: 8px; }
    .feed-title:hover { color: var(--link); text-decoration: underline; }
    .feed-summary { font-size: 0.88rem; color: var(--text-muted); line-height: 1.5; margin-bottom: 14px; }
    .feed-summary strong { color: var(--text-bold); font-weight: 600; }
    .feed-thumb { width: 100%; height: 160px; object-fit: cover; border-radius: 6px; margin-top: auto; }

    .mobile-bottom-bar { display: none; }
    @media (max-width: 768px) {
      .sidebar { position: fixed; inset: 0 auto 0 0; transform: translateX(-100%); box-shadow: 4px 0 24px rgba(0,0,0,0.6); }
      .sidebar.open { transform: translateX(0); visibility: visible !important; width: 290px !important; }
      .sidebar-backdrop.open { display: block; }
      .stream-header { padding: 12px 14px; flex-direction: column; align-items: stretch; gap: 8px; }
      .stream-header h2 { font-size: 1.1rem; white-space: normal; line-height: 1.3; }
      .header-meta-inline { font-size: 0.82rem; }
      .stream-header .menu-toggle, .stream-header .theme-toggle, .stream-header #refresh-btn { display: none !important; }
      .header-right, .search-input { width: 100%; }
      .cards-grid { grid-template-columns: 1fr; gap: 12px; padding: 12px 12px 90px; }
      .mobile-bottom-bar {
        display: flex; position: fixed; bottom: 18px; left: 50%; transform: translateX(-50%);
        background: rgba(26, 30, 36, 0.55); border: 1px solid var(--border); backdrop-filter: blur(16px);
        -webkit-backdrop-filter: blur(16px); border-radius: 36px; padding: 6px 12px; gap: 10px; z-index: 105;
        transition: transform 0.28s cubic-bezier(0.4, 0, 0.2, 1), opacity 0.25s;
      }
      .mobile-bottom-bar.bar-hidden { transform: translate(-50%, 120%); opacity: 0; pointer-events: none; }
      [data-theme="light"] .mobile-bottom-bar { background: rgba(255, 255, 255, 0.65); }
      .bottom-btn { background: transparent; border: none; color: var(--text); font-size: 1.15rem; width: 42px; height: 42px; border-radius: 50%; }
    }
  </style>
</head>
<body>
  __AUTH_OVERLAY__

  <div id="health-modal" class="modal-overlay">
    <div class="modal-card">
      <div class="modal-header"><h2>📡 Feed-Status Details</h2><button class="close-btn" onclick="toggleModal('health-modal', false)">&times;</button></div>
      <div id="health-list" class="modal-body"></div>
      <button class="modal-close-btn" onclick="toggleModal('health-modal', false)">Schließen</button>
    </div>
  </div>

  <div id="duplicate-modal" class="modal-overlay">
    <div class="modal-card">
      <div class="modal-header"><h2>🧹 Bereinigte Duplikate</h2><button class="close-btn" onclick="toggleModal('duplicate-modal', false)">&times;</button></div>
      <div id="duplicate-list" class="modal-body"></div>
      <button class="modal-close-btn" onclick="toggleModal('duplicate-modal', false)">Schließen</button>
    </div>
  </div>

  <div class="sidebar-backdrop" id="backdrop" onclick="toggleSidebar()"></div>

  <aside class="sidebar" id="sidebar">
    <div class="sidebar-header">
      <h1>⚡ __SIDEBAR_TITLE__</h1>
      <button class="close-btn" onclick="toggleSidebar()">&times;</button>
    </div>
    <ul class="source-list" id="source-list"></ul>
    <div class="sidebar-footer">
      <a href="__NAV_TARGET_URL__" class="nav-link-btn">__NAV_TARGET_TEXT__</a>
      __MARK_ALL_BTN__
    </div>
  </aside>

  <main class="main">
    <div class="stream-header">
      <div class="header-left">
        <button class="menu-toggle" onclick="toggleSidebar()">☰</button>
        <div class="header-title-group">
          <h2 id="current-title">Meldungen laden...</h2>
          <div class="header-meta-inline">
            <span class="meta-clickable" id="header-dup-info" onclick="toggleModal('duplicate-modal', true)">🧹 Duplikate ℹ️</span>
            __HEALTH_BLOCK__
          </div>
        </div>
      </div>
      <div class="header-right">
        <input type="search" class="search-input" id="search-box" placeholder="Durchsuchen..." oninput="filterSearch(this.value)">
        __DESKTOP_REFRESH_BTN__
        <button class="theme-toggle" onclick="toggleTheme()">🌓</button>
      </div>
    </div>
    <div id="articles-container" class="cards-grid"></div>
  </main>

  <nav class="mobile-bottom-bar" aria-label="Mobile Navigation">
    <button class="bottom-btn" onclick="toggleSidebar()">☰</button>
    <button class="bottom-btn" onclick="focusSearch()">🔍</button>
    __MOBILE_REFRESH_BTN__
    <button class="bottom-btn" onclick="toggleTheme()">🌓</button>
  </nav>

  <script>
    window.IS_ARCHIVE = __IS_ARCHIVE__;
    const configuredSources = __CONFIGURED_SOURCES__;
    const feedHealthData = __HEALTH_DATA__;
    const buildTimestampStr = "__NOW_STR__";

    let rawEncryptedData = "", globalArticles = [], liveArticles = [], allSourceCounts = {};
    let activeSource = 'all', searchQuery = '', selectedIndex = -1;

    function escapeHtml(s) { return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }
    function hashString(str) { let h = 0; for (let i = 0; i < str.length; i++) { h = ((h << 5) - h) + str.charCodeAt(i); h |= 0; } return Math.abs(h); }

    function formatRelativeTime(isoStr) {
      if (!isoStr) return '';
      const diff = Math.floor((new Date() - new Date(isoStr)) / 1000);
      if (isNaN(diff)) return '';
      if (diff < 60) return '• gerade eben';
      if (diff < 3600) return `• vor ${Math.floor(diff / 60)}m`;
      if (diff < 86400) return `• vor ${Math.floor(diff / 3600)}h`;
      return `• vor ${Math.floor(diff / 86400)}d`;
    }

    function initTheme() {
      const t = localStorage.getItem('hub_theme') || (window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark');
      document.documentElement.setAttribute('data-theme', t);
    }
    function toggleTheme() {
      const next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', next);
      localStorage.setItem('hub_theme', next);
    }

    function initSidebarState() {
      if (window.innerWidth > 768) {
        if (localStorage.getItem('sidebar_closed') === 'true') {
          document.getElementById('sidebar').classList.add('collapsed');
        }
      }
    }

    function toggleSidebar() {
      const sb = document.getElementById('sidebar');
      if (window.innerWidth <= 768) {
        sb.classList.toggle('open');
        document.getElementById('backdrop').classList.toggle('open');
      } else {
        sb.classList.toggle('collapsed');
        localStorage.setItem('sidebar_closed', sb.classList.contains('collapsed'));
      }
    }
    function focusSearch() { const el = document.getElementById('search-box'); if (el) { el.focus(); el.scrollIntoView({ behavior: 'smooth' }); } }
    function toggleModal(id, open) { document.getElementById(id).style.display = open ? 'flex' : 'none'; }

    function getStorage(k) { try { return JSON.parse(localStorage.getItem(k) || '[]'); } catch(e) { return []; } }
    function setStorage(k, val) { localStorage.setItem(k, JSON.stringify(val.slice(-500))); }

    function markAsRead(id) {
      const r = getStorage('read_news');
      if (!r.includes(id)) { r.push(id); setStorage('read_news', r); }
      const el = document.querySelector(`.feed-card[data-id="${id}"]`);
      if (el) el.classList.add('read');
    }

    function toggleRead(id) {
      let r = getStorage('read_news');
      r = r.includes(id) ? r.filter(x => x !== id) : [...r, id];
      setStorage('read_news', r);
      const el = document.querySelector(`.feed-card[data-id="${id}"]`);
      if (el) el.classList.toggle('read');
    }

    function markAllAsRead() {
      const r = getStorage('read_news');
      document.querySelectorAll('.feed-card').forEach(c => {
        c.classList.add('read');
        if (!r.includes(c.dataset.id)) r.push(c.dataset.id);
      });
      setStorage('read_news', r);
    }

    function initSeenObserver() {
      const seen = getStorage('seen_news');
      seen.forEach(id => { const el = document.querySelector(`.feed-card[data-id="${id}"]`); if (el) el.classList.add('seen'); });
      const timers = new Map();
      const observer = new IntersectionObserver((entries) => {
        entries.forEach(e => {
          const id = e.target.dataset.id;
          if (!id) return;
          if (e.isIntersecting) {
            timers.set(id, setTimeout(() => {
              const s = getStorage('seen_news');
              if (!s.includes(id)) { s.push(id); setStorage('seen_news', s); }
              e.target.classList.add('seen');
              observer.unobserve(e.target);
            }, 1000));
          } else if (timers.has(id)) {
            clearTimeout(timers.get(id));
            timers.delete(id);
          }
        });
      }, { root: document.querySelector('.main'), threshold: 0.6 });

      document.querySelectorAll('.feed-card:not(.seen)').forEach(c => observer.observe(c));
    }

    function initSmartHeader() {
      const mainEl = document.querySelector('.main'), header = document.querySelector('.stream-header'), bar = document.querySelector('.mobile-bottom-bar');
      let lastY = 0;
      mainEl.addEventListener('scroll', () => {
        const y = mainEl.scrollTop;
        if (Math.abs(lastY - y) <= 6 || document.activeElement === document.getElementById('search-box')) return;
        if (y > lastY && y > 50) {
          header.classList.add('header-hidden');
          if (bar) bar.classList.remove('bar-hidden');
        } else if (y < lastY) {
          header.classList.remove('header-hidden');
          if (bar) bar.classList.toggle('bar-hidden', y > 30);
        }
        lastY = y;
      }, { passive: true });
    }

    function renderUI(articles) {
      let totalDups = 0;
      allSourceCounts = {};
      articles.forEach(a => {
        totalDups += (a.other_sources || []).length;
        allSourceCounts[a.source] = (allSourceCounts[a.source] || 0) + 1;
      });

      const prefix = window.IS_ARCHIVE ? 'Archiv' : 'Alle';
      document.getElementById('current-title').textContent = `${prefix} ${articles.length} Meldungen bis ${buildTimestampStr}`;
      document.getElementById('header-dup-info').innerHTML = `🧹 ${totalDups} Duplikate bereinigt ℹ️`;

      const sortedSources = Array.from(new Set([...configuredSources, ...Object.keys(allSourceCounts)])).sort((a, b) => (allSourceCounts[b] || 0) - (allSourceCounts[a] || 0));
      document.getElementById('source-list').innerHTML = `
        <li><button class="source-btn active" onclick="filterSource('all', this)"><span>Alle</span><span class="badge">${articles.length}</span></button></li>
      ` + sortedSources.map(s => `
        <li><button class="source-btn" onclick="filterSource('${escapeHtml(s)}', this)"><span>${escapeHtml(s)}</span><span class="badge">${allSourceCounts[s] || 0}</span></button></li>
      `).join('');

      const readList = getStorage('read_news');
      document.getElementById('articles-container').innerHTML = articles.map(a => {
        const id = hashString(a.link);
        const others = (a.other_sources && a.other_sources.length) ? `<span class="feed-others">• Auch bei: ${escapeHtml(a.other_sources.join(", "))}</span>` : '';
        const img = a.image ? `<img class="feed-thumb" src="${a.image}" loading="lazy" alt="Thumbnail" onerror="this.remove()">` : '';
        const isRead = readList.includes(String(id)) ? ' read' : '';
        return `
          <article class="feed-card${isRead}" data-id="${id}" data-sources="${escapeHtml([a.source, ...(a.other_sources || [])].join(';;;'))}">
            <div class="feed-content">
              <div class="feed-meta"><span class="feed-source">${escapeHtml(a.source)}</span><span class="feed-time">${formatRelativeTime(a.published)}</span>${others}</div>
              <a class="feed-title" href="${escapeHtml(a.link)}" target="_blank" rel="noopener" onclick="markAsRead('${id}')">${escapeHtml(a.title)}</a>
              <p class="feed-summary">${a.summary}</p>
            </div>
            ${img}
          </article>
        `;
      }).join('');
    }

    function filterSource(src, btn) {
      activeSource = src;
      document.querySelectorAll('.source-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const prefix = window.IS_ARCHIVE ? 'Archiv' : 'Alle';
      document.getElementById('current-title').textContent = (src === 'all')
        ? `${prefix} ${liveArticles.length} Meldungen bis ${buildTimestampStr}`
        : `${src} (${allSourceCounts[src] || 0}) bis ${buildTimestampStr}`;
      applyFilters();
      if (window.innerWidth <= 768) toggleSidebar();
    }

    function filterSearch(q) { searchQuery = q.toLowerCase().trim(); applyFilters(); }
    function applyFilters() {
      document.querySelectorAll('.feed-card').forEach(c => {
        const matchSrc = activeSource === 'all' || (c.dataset.sources || '').split(';;;').includes(activeSource);
        const matchQ = !searchQuery || c.textContent.toLowerCase().includes(searchQuery);
        c.style.display = (matchSrc && matchQ) ? '' : 'none';
      });
    }

    async function triggerWorkflow() {
      let token = localStorage.getItem('gh_dispatch_token') || prompt("GitHub Personal Access Token:");
      if (!token) return;
      localStorage.setItem('gh_dispatch_token', token.trim());
      try {
        const res = await fetch('https://api.github.com/repos/schoerb/news-hub/actions/workflows/deploy.yml/dispatches', {
          method: 'POST',
          headers: { 'Accept': 'application/vnd.github+json', 'Authorization': `Bearer ${token}` },
          body: JSON.stringify({ ref: 'main' })
        });
        alert(res.status === 204 ? '🚀 Action gestartet! Dauer: ~1 Min.' : `Status: ${res.status}`);
      } catch (e) { alert(e.message); }
    }

    async function init() {
      initTheme();
      initSidebarState();
      try {
        const r = await fetch('data.json');
        rawEncryptedData = await r.text();
      } catch (e) { return; }

      const pw = localStorage.getItem('hub_key');
      if (rawEncryptedData.trim().startsWith('[')) {
        globalArticles = JSON.parse(rawEncryptedData);
      } else if (pw) {
        try { globalArticles = JSON.parse(CryptoJS.AES.decrypt(rawEncryptedData, pw).toString(CryptoJS.enc.Utf8)); } catch(e) {}
      }

      if (!globalArticles.length) {
        document.getElementById('auth-overlay').style.display = 'flex';
        return;
      }
      onReady();
    }

    function submitAuth() {
      const pw = document.getElementById('auth-password').value;
      try {
        globalArticles = JSON.parse(CryptoJS.AES.decrypt(rawEncryptedData, pw).toString(CryptoJS.enc.Utf8));
        localStorage.setItem('hub_key', pw);
        document.getElementById('auth-overlay').style.display = 'none';
        onReady();
      } catch(e) { document.getElementById('auth-error').style.display = 'block'; }
    }

    function onReady() {
      const now = Date.now();
      const cutoff24 = new Date(now - 24 * 3600 * 1000);
      const cutoff48 = new Date(now - 48 * 3600 * 1000);

      liveArticles = globalArticles.filter(a => {
        const p = new Date(a.published);
        return window.IS_ARCHIVE ? (p < cutoff24 && p >= cutoff48) : (p >= cutoff24);
      });

      renderUI(liveArticles);
      initSeenObserver();
      initSmartHeader();

      // Duplikate Modal Liste
      const dupMap = {};
      liveArticles.forEach(a => (a.merged_details || []).forEach(m => {
        dupMap[m.source] = dupMap[m.source] || [];
        dupMap[m.source].push(m);
      }));
      document.getElementById('duplicate-list').innerHTML = Object.entries(dupMap).map(([src, items]) => `
        <div class="modal-row"><span style="font-weight:600">${escapeHtml(src)}</span><span class="badge">${items.length}</span></div>
      `).join('') || '<p style="color:var(--text-muted); padding:10px 0;">Keine Dubletten vorhanden.</p>';

      // Health Modal Liste mit genauen Status-Codes
      document.getElementById('health-list').innerHTML = feedHealthData.map(f => {
        const isOk = f.status === 'ok' || f.code === 304 || f.code === 200;
        const icon = isOk ? '🟢' : '🔴';
        const info = (f.code === 304) ? 'HTTP 304 (Cache unverändert)' : (isOk ? `HTTP ${f.code}` : `Fehler: ${f.status} (${f.code})`);
        return `
          <div class="modal-row">
            <span style="font-weight:500">${icon} ${escapeHtml(f.title)}</span>
            <span style="color:${isOk ? 'var(--text-muted)' : '#ef4444'}; font-family:monospace; font-size:0.8rem">${escapeHtml(info)}</span>
          </div>
        `;
      }).join('');
    }

    document.addEventListener('keydown', (e) => {
      if (document.activeElement === document.getElementById('search-box')) return;
      const visible = Array.from(document.querySelectorAll('.feed-card')).filter(c => c.style.display !== 'none');
      if (e.key === '[') toggleSidebar();
      if (e.key === 'j' && visible.length) { selectedIndex = Math.min(selectedIndex + 1, visible.length - 1); visible[selectedIndex].scrollIntoView({ behavior: 'smooth', block: 'nearest' }); }
      if (e.key === 'k' && visible.length) { selectedIndex = Math.max(selectedIndex - 1, 0); visible[selectedIndex].scrollIntoView({ behavior: 'smooth', block: 'nearest' }); }
      if (e.key === 'o' && selectedIndex >= 0) window.open(visible[selectedIndex].querySelector('.feed-title').href, '_blank');
      if (e.key === 'm' && selectedIndex >= 0) toggleRead(visible[selectedIndex].dataset.id);
      if (e.key === '/') { e.preventDefault(); focusSearch(); }
    });

    document.addEventListener('DOMContentLoaded', init);
  </script>
</body>
</html>
"""


def render_page(feed_health, feeds, is_archive=False):
    now_str = datetime.datetime.now(BERLIN_TZ).strftime("%d.%m.%Y, %H:%M")
    ok_feeds = sum(1 for h in feed_health if h["status"] == "ok" or h["code"] in (200, 304))
    failed_count = len(feed_health) - ok_feeds

    if failed_count > 0:
        health_text = (
            f'<span class="meta-sep">•</span>'
            f'<span style="color:#eab308; cursor:pointer;" onclick="toggleModal(\'health-modal\', true)" title="Klicken für Fehlerdetails">'
            f'🟡 {ok_feeds}/{len(feed_health)} Feeds ({failed_count} gestört) ℹ️</span>'
        )
    else:
        health_text = (
            f'<span class="meta-sep">•</span>'
            f'<span class="meta-clickable" onclick="toggleModal(\'health-modal\', true)" title="Klicken für Feed-Details">'
            f'🟢 {ok_feeds}/{len(feed_health)} Feeds online ℹ️</span>'
        )

    auth_overlay = """
    <div id="auth-overlay" class="modal-overlay" style="display:none">
      <div class="modal-card" style="text-align:center">
        <h2 style="margin-bottom:8px">🔐 Geschützt</h2>
        <input type="password" id="auth-password" class="search-input" style="width:100%; margin-bottom:12px" placeholder="Passwort..." onkeydown="if(event.key==='Enter') submitAuth()">
        <button class="modal-close-btn" style="margin-top:0" onclick="submitAuth()">Entschlüsseln</button>
        <div id="auth-error" style="color:#ef4444; font-size:0.8rem; margin-top:8px; display:none">Ungültiges Passwort!</div>
      </div>
    </div>
    """

    page = PAGE_TEMPLATE.replace("__PAGE_TITLE__", "Archiv" if is_archive else "News-Hub") \
                         .replace("__SIDEBAR_TITLE__", "Archiv (24–48h)" if is_archive else "News-Hub") \
                         .replace("__NAV_TARGET_URL__", "index.html" if is_archive else "archive.html") \
                         .replace("__NAV_TARGET_TEXT__", "← Zum Live-Feed" if is_archive else "📑 Zum Archiv (24–48h)") \
                         .replace("__MARK_ALL_BTN__", "" if is_archive else '<button class="mark-all-btn" onclick="markAllAsRead()">✓ Alle als gelesen markieren</button>') \
                         .replace("__DESKTOP_REFRESH_BTN__", "" if is_archive else '<button class="menu-toggle" id="refresh-btn" onclick="triggerWorkflow()">🔄</button>') \
                         .replace("__MOBILE_REFRESH_BTN__", "" if is_archive else '<button class="bottom-btn" id="mobile-refresh-btn" onclick="triggerWorkflow()">🔄</button>') \
                         .replace("__AUTH_OVERLAY__", auth_overlay) \
                         .replace("__NOW_STR__", now_str) \
                         .replace("__HEALTH_BLOCK__", health_text) \
                         .replace("__HEALTH_DATA__", json.dumps(feed_health, ensure_ascii=False)) \
                         .replace("__CONFIGURED_SOURCES__", json.dumps([f["title"] for f in feeds], ensure_ascii=False)) \
                         .replace("__IS_ARCHIVE__", "true" if is_archive else "false")
    return page


if __name__ == "__main__":
    os.makedirs("public", exist_ok=True)
    page_password = os.environ.get("PAGE_PASSWORD", "")

    cached_articles, cache_meta = load_cached_state()
    cached_articles = expire_old_articles(cached_articles)
    feeds = parse_opml()

    raw_feed_items, updated_cache_meta, feed_health = fetch_all_feeds(feeds, cache_meta)
    with open("cache_meta.json", "w", encoding="utf-8") as f:
        json.dump(updated_cache_meta, f, separators=(',', ':'))

    truly_new_items = []
    for raw in raw_feed_items:
        if any(raw["link"] == c["link"] for c in cached_articles):
            continue
        matched_cached = next((c for c in cached_articles if is_duplicate(raw["title"], c["title"])), None)
        if matched_cached:
            others = matched_cached.setdefault("other_sources", [])
            if raw["source"] != matched_cached["source"] and raw["source"] not in others:
                others.append(raw["source"])
            matched_cached.setdefault("merged_details", []).append({
                "source": raw.get("source", "Unbekannt"), "title": raw.get("title", ""),
                "link": raw.get("link", ""), "matched_with": matched_cached.get("title", "")
            })
        else:
            truly_new_items.append(raw)

    print(f"📦 Neue Unikate: {len(truly_new_items)} (Cache: {len(cached_articles)})")
    bundled_new = consolidate_articles(truly_new_items)
    combined = (summarize_delta_with_gemini(bundled_new) + cached_articles) if bundled_new else cached_articles
    final_articles = sorted(consolidate_articles(combined), key=lambda a: a.get("_ts", 0), reverse=True)

    frontend_articles = []
    for a in final_articles:
        item = {
            "title": a["title"],
            "link": a["link"],
            "source": a["source"],
            "summary": a["summary"],
            "published": a.get("published"),
        }
        if a.get("image"):
            item["image"] = a["image"]
        if a.get("other_sources"):
            item["other_sources"] = a["other_sources"]
        if a.get("merged_details"):
            item["merged_details"] = a["merged_details"]
        frontend_articles.append(item)

    if "GITHUB_OUTPUT" in os.environ:
        has_changes = bool(bundled_new) or (len(cached_articles) != len(final_articles))
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as gh_out:
            gh_out.write(f"deploy={'true' if has_changes else 'false'}\n")

    articles_json = json.dumps(frontend_articles, ensure_ascii=False, separators=(',', ':'))
    with open("public/data.json", "w", encoding="utf-8") as f:
        f.write(encrypt_payload(articles_json, page_password) if page_password else articles_json)

    with open("public/index.html", "w", encoding="utf-8") as f:
        f.write(render_page(feed_health, feeds, is_archive=False))

    with open("public/archive.html", "w", encoding="utf-8") as f:
        f.write(render_page(feed_health, feeds, is_archive=True))
