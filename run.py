#!/usr/bin/env python3
"""
Firebase Hunter v4.2
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✨ NEW: Auto Random mode (Firebase-style random IDs)
✨ NEW: Elapsed time in job API
✅ Active URLs file me save
✅ TG/Discord pe sirf verified
✅ Data survives restart
✅ 32 workers default
"""
from __future__ import annotations
import http.client
import json
import os
import random
import re
import secrets
import sqlite3
import string
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qsl, urlencode
from urllib.request import Request, urlopen

import requests

# ═══════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════
BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
DATA.mkdir(exist_ok=True)
DB = DATA / "bulk.sqlite3"
ACTIVE_FILE = DATA / "active.txt"
VERIFIED_FILE = DATA / "valid.txt"

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8080"))
WORKERS = max(1, min(128, int(os.getenv("BULK_WORKERS", "32"))))
MAX_URLS = max(1, int(os.getenv("BULK_MAX_URLS", "100000")))
MAX_BYTES = max(4096, int(os.getenv("BULK_MAX_BYTES", "10485760")))
TIMEOUT = max(2, min(30, int(os.getenv("BULK_TIMEOUT", "6"))))
MAX_PROBE = 64 * 1024
RATE_WINDOW = 60
RATE_LIMIT = int(os.getenv("BULK_RATE_LIMIT", "10000"))
RATE: dict = {}
RATE_LOCK = threading.Lock()
STOP = threading.Event()
LOG_LOCK = threading.Lock()
GEN_LOCK = threading.Lock()

AUTO_MAX_URLS = max(100, int(os.getenv("BULK_AUTO_MAX_URLS", "10000000")))
AUTO_BATCH = max(50, min(10000, int(os.getenv("BULK_AUTO_BATCH", "500"))))
AUTO_EXPAND = os.getenv("BULK_AUTO_EXPAND", "1") == "1"
AUTO_MAX_PARTS = 3
AUTO_MAX_LEN = 40

FIREBASE_DEFAULT_KEY = os.getenv("FIREBASE_DEFAULT_KEY", "12")
VERIFY_TIMEOUT = 10
VERIFY_KEYS = [FIREBASE_DEFAULT_KEY, "", None]

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "").strip()
PUBLIC_BASE_URL = (os.getenv("PUBLIC_BASE_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").strip().rstrip("/")
TELEGRAM_ENABLED = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)
DISCORD_ENABLED = bool(DISCORD_WEBHOOK)
FILE_INTERVAL = int(os.getenv("FILE_INTERVAL_MIN", "15")) * 60

ACTIVE_URLS: set = set()
VERIFIED_URLS: set = set()
ACTIVE_LOCK = threading.Lock()
VERIFIED_LOCK = threading.Lock()
_TLS = threading.local()

PREFIXES = ["admin-", "panel-", "user-", "staff-", "test-", "new-", "my-",
            "the-", "official-", "real-", "pro-", "super-", "best-", "indian-"]
SUFFIXES_CLEAN = ["-app", "-admin", "-panel", "-user", "-pro", "-official",
                  "-india", "-in", "-new", "-test", "-main", "-bot", "-web",
                  "-site", "-dash"]
MISSPELLS = ["pannel", "panal", "admen", "admain", "adminstrator"]
FB_SUFFIXES = ["-default-rtdb.firebaseio.com"]

# ─── RANDOM MODE POOLS (Firebase-style IDs) ───
RANDOM_WORDS = [
    "app", "test", "demo", "admin", "panel", "chat", "user", "data",
    "my", "new", "the", "web", "shop", "blog", "game", "hotel",
    "food", "cafe", "store", "site", "note", "todo", "task", "list",
    "quick", "smart", "fast", "easy", "cool", "nice", "good", "best",
    "fir", "fb", "dev", "prod", "beta", "alpha", "core", "main",
    "india", "in", "desi", "local", "global", "pro", "plus", "max",
    "team", "work", "home", "office", "school", "college", "study",
    "music", "video", "photo", "book", "foodie", "shopee", "kart",
    "pay", "money", "cash", "bank", "wallet", "loan", "bill", "tax",
]
RANDOM_HEX = "0123456789abcdef"
RANDOM_LOWER = string.ascii_lowercase
RANDOM_DIGITS = "0123456789"

RE_RTDB     = re.compile(r'^([a-z0-9][a-z0-9\-]*)\-rtdb\.firebaseio\.com$', re.I)
RE_REGIONAL = re.compile(r'^([a-z0-9][a-z0-9\-]*)\-default\-rtdb\.[a-z0-9\-]+\.firebasedatabase\.app$', re.I)
RE_LEGACY   = re.compile(r'^([a-z0-9][a-z0-9\-]*)\.firebaseio\.com$', re.I)


# ═══════════════════════════════════════════════════════════════
#  SCHEMA
# ═══════════════════════════════════════════════════════════════
SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 token TEXT UNIQUE NOT NULL,
 filename TEXT NOT NULL,
 total INTEGER NOT NULL,
 processed INTEGER NOT NULL DEFAULT 0,
 active INTEGER NOT NULL DEFAULT 0,
 inactive INTEGER NOT NULL DEFAULT 0,
 no_data INTEGER NOT NULL DEFAULT 0,
 unreachable INTEGER NOT NULL DEFAULT 0,
 too_large INTEGER NOT NULL DEFAULT 0,
 status TEXT NOT NULL DEFAULT 'pending',
 error TEXT,
 job_type TEXT NOT NULL DEFAULT 'manual',
 seeds TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 started_at TEXT,
 completed_at TEXT,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS items (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 job_id INTEGER NOT NULL,
 url TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending',
 http_status INTEGER,
 error TEXT,
 latency_ms INTEGER,
 checked_at TEXT,
 FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_items_pending ON items(job_id,status,id);
CREATE INDEX IF NOT EXISTS idx_items_active ON items(job_id,status);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status,id);

CREATE TABLE IF NOT EXISTS active_urls (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 url TEXT UNIQUE NOT NULL,
 first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 source_job_id INTEGER,
 http_status INTEGER,
 latency_ms INTEGER,
 device_count INTEGER DEFAULT -1
);
CREATE INDEX IF NOT EXISTS idx_active_urls_url ON active_urls(url);

CREATE TABLE IF NOT EXISTS verified_urls (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 url TEXT UNIQUE NOT NULL,
 first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 source_job_id INTEGER,
 device_count INTEGER DEFAULT 0,
 http_status INTEGER,
 latency_ms INTEGER
);
CREATE INDEX IF NOT EXISTS idx_verified_urls_url ON verified_urls(url);

CREATE TABLE IF NOT EXISTS seed_stats (
 seed TEXT PRIMARY KEY,
 attempts INTEGER NOT NULL DEFAULT 0,
 active INTEGER NOT NULL DEFAULT 0,
 last_hit_at TEXT,
 first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_seed_stats_rate ON seed_stats(active, attempts);
"""


# ═══════════════════════════════════════════════════════════════
#  UTILITIES
# ═══════════════════════════════════════════════════════════════
def log(msg: str):
    with LOG_LOCK:
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] {msg}", flush=True)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def elapsed_seconds(started_at: str | None, ended_at: str | None = None) -> int:
    """Compute seconds between start and end (or now)."""
    if not started_at:
        return 0
    try:
        s = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    except Exception:
        return 0
    if ended_at:
        try:
            e = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
        except Exception:
            e = datetime.now(timezone.utc)
    else:
        e = datetime.now(timezone.utc)
    if s.tzinfo is None: s = s.replace(tzinfo=timezone.utc)
    if e.tzinfo is None: e = e.replace(tzinfo=timezone.utc)
    return max(0, int((e - s).total_seconds()))


def db():
    c = sqlite3.connect(DB, timeout=60, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA busy_timeout=60000")
    return c


def init_db():
    c = db()
    c.executescript(SCHEMA)
    cols = {r["name"] for r in c.execute("PRAGMA table_info(jobs)").fetchall()}
    if "job_type" not in cols:
        try: c.execute("ALTER TABLE jobs ADD COLUMN job_type TEXT NOT NULL DEFAULT 'manual'")
        except sqlite3.OperationalError: pass
    if "seeds" not in cols:
        try: c.execute("ALTER TABLE jobs ADD COLUMN seeds TEXT")
        except sqlite3.OperationalError: pass
    c.execute("UPDATE jobs SET status='pending', started_at=NULL WHERE status='processing' AND job_type='manual'")
    c.execute("UPDATE jobs SET status='stopped' WHERE status='processing' AND job_type IN ('auto','auto-random')")
    c.close()


# ═══════════════════════════════════════════════════════════════
#  PERSISTENCE
# ═══════════════════════════════════════════════════════════════
def atomic_write_file(path: Path, lines: list):
    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + ("\n" if lines else ""))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception as e:
        log(f"atomic_write_file error: {e}")


def load_all_persistent():
    try:
        c = db()
        arows = c.execute("SELECT url FROM active_urls ORDER BY id").fetchall()
        vrows = c.execute("SELECT url FROM verified_urls ORDER BY id").fetchall()
        c.close()
        with ACTIVE_LOCK: ACTIVE_URLS.update(r["url"] for r in arows)
        with VERIFIED_LOCK: VERIFIED_URLS.update(r["url"] for r in vrows)
    except Exception as e:
        log(f"load DB error: {e}")

    for fpath, target, lock in [
        (ACTIVE_FILE, ACTIVE_URLS, ACTIVE_LOCK),
        (VERIFIED_FILE, VERIFIED_URLS, VERIFIED_LOCK),
    ]:
        if fpath.exists():
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    urls = [line.strip() for line in f if line.strip()]
                with lock: target.update(urls)
            except Exception as e:
                log(f"load {fpath.name} error: {e}")

    log(f"📦 Loaded {len(ACTIVE_URLS)} active, {len(VERIFIED_URLS)} verified")


def persist_active(url, source_job_id=None, http_status=None, latency_ms=None):
    url = str(url).strip().rstrip("/")
    if not url: return False
    with ACTIVE_LOCK:
        if url in ACTIVE_URLS:
            try:
                c = db()
                c.execute("UPDATE active_urls SET last_seen_at=?, source_job_id=? WHERE url=?",
                          (now(), source_job_id, url))
                c.close()
            except Exception: pass
            return False
        try:
            c = db()
            c.execute("INSERT OR IGNORE INTO active_urls"
                      "(url,last_seen_at,source_job_id,http_status,latency_ms) VALUES(?,?,?,?,?)",
                      (url, now(), source_job_id, http_status, latency_ms))
            inserted = c.total_changes == 1
            c.close()
        except Exception as e:
            log(f"persist_active error: {e}"); return False
        if not inserted:
            ACTIVE_URLS.add(url); return False
        ACTIVE_URLS.add(url)
    try:
        with open(ACTIVE_FILE, "a", encoding="utf-8") as f:
            f.write(url + "\n")
    except Exception as e:
        log(f"active.txt write error: {e}")
    return True


def persist_verified(url, source_job_id=None, device_count=0, http_status=None, latency_ms=None):
    url = str(url).strip().rstrip("/")
    if not url: return False
    with VERIFIED_LOCK:
        if url in VERIFIED_URLS:
            try:
                c = db()
                c.execute("UPDATE verified_urls SET last_seen_at=?, source_job_id=?, "
                          "device_count=MAX(device_count,?) WHERE url=?",
                          (now(), source_job_id, device_count, url))
                c.close()
            except Exception: pass
            return False
        try:
            c = db()
            c.execute("INSERT OR IGNORE INTO verified_urls"
                      "(url,last_seen_at,source_job_id,device_count,http_status,latency_ms) "
                      "VALUES(?,?,?,?,?,?)",
                      (url, now(), source_job_id, device_count, http_status, latency_ms))
            inserted = c.total_changes == 1
            c.close()
        except Exception as e:
            log(f"persist_verified error: {e}"); return False
        if not inserted:
            VERIFIED_URLS.add(url); return False
        VERIFIED_URLS.add(url)
    try:
        with open(VERIFIED_FILE, "a", encoding="utf-8") as f:
            f.write(url + "\n")
    except Exception as e:
        log(f"valid.txt write error: {e}")
    return True


def save_all_files():
    with ACTIVE_LOCK: atomic_write_file(ACTIVE_FILE, sorted(ACTIVE_URLS))
    with VERIFIED_LOCK: atomic_write_file(VERIFIED_FILE, sorted(VERIFIED_URLS))


# ═══════════════════════════════════════════════════════════════
#  SEED PATTERN LEARNING
# ═══════════════════════════════════════════════════════════════
def record_seed_attempt(seed: str, is_active: bool):
    if not seed: return
    seed = seed.strip().lower()[:60]
    try:
        c = db()
        c.execute("INSERT INTO seed_stats(seed, attempts, active) VALUES(?, 1, ?) "
                  "ON CONFLICT(seed) DO UPDATE SET "
                  "attempts = attempts + 1, active = active + ?, "
                  "last_hit_at = CASE WHEN ? = 1 THEN ? ELSE last_hit_at END",
                  (seed, 1 if is_active else 0, 1 if is_active else 0,
                   1 if is_active else 0, now()))
        c.close()
    except Exception as e:
        log(f"record_seed_attempt error: {e}")


def get_top_seeds(limit=50):
    c = db()
    rows = c.execute(
        "SELECT seed, attempts, active, "
        "ROUND(CAST(active AS FLOAT) / MAX(attempts,1) * 100, 2) AS rate "
        "FROM seed_stats WHERE attempts >= 3 "
        "ORDER BY rate DESC, active DESC LIMIT ?", (limit,)).fetchall()
    c.close()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════
#  URL CANONICALIZATION
# ═══════════════════════════════════════════════════════════════
def canonical_host(host):
    if not host: return None
    host = host.lower().rstrip(".")
    m = RE_REGIONAL.match(host)
    if m:
        p = m.group(1)
        if not p.endswith("-default"): p += "-default"
        return f"{p}-rtdb.firebaseio.com"
    if RE_RTDB.match(host): return host
    if RE_LEGACY.match(host): return None
    return None


def canonical_url(raw):
    if not raw: return None
    raw = raw.strip()
    if not raw or raw.startswith("#"): return None
    if not re.match(r"^https?://", raw, re.I):
        raw = "https://" + raw
    raw = re.sub(r"^http://", "https://", raw, flags=re.I)
    try: u = urlparse(raw)
    except Exception: return None
    if u.username or u.password: return None
    canon = canonical_host(u.hostname or "")
    if not canon: return None
    return f"https://{canon}"


# ═══════════════════════════════════════════════════════════════
#  FIREBASE VERIFY
# ═══════════════════════════════════════════════════════════════
def verify_devices(url, timeout=VERIFY_TIMEOUT):
    best = -1
    for key in VERIFY_KEYS:
        endpoint = f"{url.rstrip('/')}/clients.json"
        params = {"auth": key} if key else {}
        try:
            r = requests.get(endpoint, params=params,
                headers={"Accept": "application/json", "User-Agent": "FB-Checker/4.2"},
                timeout=timeout)
            if r.status_code in (401, 403, 404): continue
            r.raise_for_status()
            try: data = r.json()
            except ValueError: continue
            if data is None:
                best = max(best, 0); continue
            if isinstance(data, dict):
                best = max(best, len(data))
                if len(data) > 0: return len(data)
        except Exception:
            continue
    return best


# ═══════════════════════════════════════════════════════════════
#  NOTIFICATIONS
# ═══════════════════════════════════════════════════════════════
def _tg_send(text, reply_markup=None):
    if not TELEGRAM_ENABLED: return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text,
                   "parse_mode": "HTML", "disable_web_page_preview": True}
        if reply_markup: payload["reply_markup"] = reply_markup
        req = Request(url, data=json.dumps(payload).encode(),
                      headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=8) as r: r.read()
    except Exception as e:
        log(f"TG error: {type(e).__name__}: {str(e)[:100]}")


def tg_notify(text, reply_markup=None):
    if not TELEGRAM_ENABLED: return
    threading.Thread(target=_tg_send, args=(text, reply_markup), daemon=True).start()


def _dc_send(content=None, embeds=None):
    if not DISCORD_ENABLED: return
    try:
        payload = {}
        if content: payload["content"] = content[:1900]
        if embeds: payload["embeds"] = embeds[:10]
        req = Request(DISCORD_WEBHOOK, data=json.dumps(payload).encode(),
                      headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=8) as r: r.read()
    except Exception as e:
        log(f"Discord error: {type(e).__name__}: {str(e)[:100]}")


def dc_notify(text, embeds=None):
    if not DISCORD_ENABLED: return
    threading.Thread(target=_dc_send, args=(text, embeds), daemon=True).start()


def tg_send_document(filepath, caption=""):
    if not TELEGRAM_ENABLED: return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
        with open(filepath, "rb") as f:
            files = {"document": (os.path.basename(filepath), f, "text/plain")}
            data = {"chat_id": TELEGRAM_CHAT_ID, "caption": caption, "parse_mode": "HTML"}
            requests.post(url, files=files, data=data, timeout=60)
    except Exception as e:
        log(f"TG file error: {type(e).__name__}: {str(e)[:100]}")


def tg_keyboard():
    if not PUBLIC_BASE_URL: return None
    return {"inline_keyboard": [
        [{"text": "✅ Verified URLs (.txt)",
          "url": PUBLIC_BASE_URL + "/api/public-bulk/verified.txt"}],
        [{"text": "📊 Stats",
          "url": PUBLIC_BASE_URL + "/api/public-bulk/stats"}],
    ]}


def tg_notify_verified_count(reason="Updated"):
    if not TELEGRAM_ENABLED: return
    with VERIFIED_LOCK: v = len(VERIFIED_URLS)
    msg = (f"📦 <b>{reason}</b>\n"
           f"━━━━━━━━━━━━━━━━━━━━━━━\n"
           f"✅ Verified (with devices): <b>{v}</b>\n"
           f"🕐 {now()}")
    tg_notify(msg, tg_keyboard())
    if DISCORD_ENABLED:
        dc_notify("", embeds=[{
            "title": f"📦 {reason}", "color": 0x10b981,
            "fields": [{"name": "Verified", "value": str(v), "inline": True}],
            "timestamp": now()
        }])


def tg_notify_verified_new(url, http_code, latency_ms, device_count):
    if TELEGRAM_ENABLED:
        msg = (
            f"🔥 <b>VERIFIED FIREBASE</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"<code>{url}</code>\n\n"
            f"<b>Devices:</b> {device_count} ✅\n"
            f"<b>HTTP:</b> {http_code}\n"
            f"<b>Latency:</b> {latency_ms if latency_ms is not None else '—'} ms\n"
            f'🔗 <a href="{url}/.json?shallow=true">Open JSON</a>'
        )
        tg_notify(msg)
    if DISCORD_ENABLED:
        dc_notify("", embeds=[{
            "title": "🔥 VERIFIED FIREBASE",
            "description": f"```\n{url}\n```",
            "color": 0x10b981,
            "fields": [
                {"name": "Devices", "value": str(device_count), "inline": True},
                {"name": "HTTP", "value": str(http_code), "inline": True},
                {"name": "Latency", "value": f"{latency_ms or '—'} ms", "inline": True},
            ],
            "url": f"{url}/.json?shallow=true",
            "timestamp": now()
        }])


def tg_startup_msg():
    if not TELEGRAM_ENABLED: return
    with VERIFIED_LOCK: v = len(VERIFIED_URLS)
    with ACTIVE_LOCK: a = len(ACTIVE_URLS)
    msg = (f"🚀 <b>Firebase Hunter v4.2</b>\n"
           f"Workers: {WORKERS} | Timeout: {TIMEOUT}s\n"
           f"Auto cap: {AUTO_MAX_URLS:,}\n"
           f"Loaded: ✅ {v} verified · 📁 {a} active")
    tg_notify(msg, tg_keyboard())


# ═══════════════════════════════════════════════════════════════
#  PERIODIC FILE SENDER
# ═══════════════════════════════════════════════════════════════
def periodic_file_sender():
    log(f"📤 File sender started (every {FILE_INTERVAL // 60} min)")
    next_send = time.time() + FILE_INTERVAL
    while not STOP.is_set():
        wait = next_send - time.time()
        if wait > 0:
            if STOP.wait(min(wait, 5)): return
            continue
        try:
            save_all_files()
            if TELEGRAM_ENABLED:
                with VERIFIED_LOCK: v = len(VERIFIED_URLS)
                if v > 0 and VERIFIED_FILE.exists():
                    tg_send_document(str(VERIFIED_FILE),
                        f"✅ <b>Verified URLs</b>\nTotal: <b>{v}</b>\n{now()}")
                    log(f"📤 Sent valid.txt ({v} verified)")
        except Exception as e:
            log(f"periodic_file_sender error: {e}")
        next_send = time.time() + FILE_INTERVAL


# ═══════════════════════════════════════════════════════════════
#  URL PARSING
# ═══════════════════════════════════════════════════════════════
def normalize(line):
    line = line.strip()
    if not line or line.startswith("#"): return None
    candidate = line.split(",", 1)[0].strip().strip('"').strip("'")
    if candidate.lower() in ("url", "firebase_url", "firebase url"): return None
    return canonical_url(candidate)


def parse_urls(content):
    seen, out = set(), []
    for line in content.splitlines():
        u = normalize(line)
        if u and u not in seen:
            seen.add(u); out.append(u)
            if len(out) >= MAX_URLS: break
    return out


# ═══════════════════════════════════════════════════════════════
#  PROBE
# ═══════════════════════════════════════════════════════════════
def _get_conn(host, port):
    cache = getattr(_TLS, "conn", None)
    if cache and cache[0] == (host, port): return cache[1]
    if cache:
        try: cache[1].close()
        except Exception: pass
    conn = http.client.HTTPSConnection(host, port, timeout=TIMEOUT)
    _TLS.conn = ((host, port), conn)
    return conn


def probe(url):
    started = time.monotonic()
    try:
        u = urlparse(url)
        host, port = u.hostname, u.port or 443
        base = u.path or "/"
        if not base.endswith(".json"):
            base = base.rstrip("/") + "/.json" if base != "/" else "/.json"
        q = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True)
             if k.lower() not in {"orderby", "limittofirst"}]
        q += [("orderBy", '"$key"'), ("limitToFirst", "1")]
        path = base + "?" + urlencode(q)

        def _do():
            conn = _get_conn(host, port)
            conn.request("GET", path, headers={"Accept": "application/json", "User-Agent": "FB-Checker/4.2"})
            return conn.getresponse()

        try:
            resp = _do()
        except (http.client.HTTPException, ConnectionError, OSError):
            _TLS.conn = None
            resp = _do()

        code = resp.status
        latency = int((time.monotonic() - started) * 1000)
        if code != 200:
            resp.read(16 * 1024)
            return "inactive", code, f"HTTP {code}", latency

        chunks, total = [], 0
        while total <= MAX_PROBE:
            chunk = resp.read(min(8192, MAX_PROBE + 1 - total))
            if not chunk: break
            chunks.append(chunk); total += len(chunk)
            if total > MAX_PROBE:
                return "response_too_large", code, "Response > 64KB", latency

        body = b"".join(chunks)
        if not body.strip(): return "invalid_no_data", code, "Empty response", latency
        try: parsed = json.loads(body.decode("utf-8", "strict"))
        except Exception: return "invalid_no_data", code, "Invalid JSON", latency
        if parsed in (None, "", {}, [], False, 0):
            return "invalid_no_data", code, "No data", latency
        return "active", code, "HTTP 200 + data", latency
    except Exception as e:
        return ("unreachable", None, f"{type(e).__name__}: {str(e)[:150]}",
                int((time.monotonic() - started) * 1000))


# ═══════════════════════════════════════════════════════════════
#  RANDOM FIREBASE-STYLE ID GENERATOR
# ═══════════════════════════════════════════════════════════════
def random_project_id():
    """
    Generate Firebase-style random project ID (like auto-generated ones).
    Patterns:
      - abcde-4f8a2         (letters + hex)
      - myapp-8228a         (word + hex)
      - jpicku-47790        (word + 5digits)
      - hloo-acc63          (random letters + hex)
      - abc123d             (mixed)
      - apptest             (word + word)
    """
    style = random.randint(0, 5)

    if style == 0:
        # abcde-4f8a2
        n = random.randint(4, 8)
        letters = ''.join(random.choices(RANDOM_LOWER, k=n))
        hex5 = ''.join(random.choices(RANDOM_HEX, k=5))
        return f"{letters}-{hex5}"

    if style == 1:
        # myapp-8228a
        w = random.choice(RANDOM_WORDS)
        hex5 = ''.join(random.choices(RANDOM_HEX, k=5))
        return f"{w}-{hex5}"

    if style == 2:
        # jpicku-47790
        w = ''.join(random.choices(RANDOM_LOWER, k=random.randint(4, 7)))
        d5 = str(random.randint(10000, 99999))
        return f"{w}-{d5}"

    if style == 3:
        # hloo-acc63
        w1 = ''.join(random.choices(RANDOM_LOWER, k=random.randint(3, 6)))
        w2 = ''.join(random.choices(RANDOM_HEX, k=5))
        return f"{w1}-{w2}"

    if style == 4:
        # abc123d
        letters = ''.join(random.choices(RANDOM_LOWER, k=random.randint(3, 5)))
        digits = ''.join(random.choices(RANDOM_DIGITS, k=random.randint(2, 3)))
        last = random.choice(RANDOM_LOWER)
        return f"{letters}{digits}{last}"

    # style 5: apptest
    return random.choice(RANDOM_WORDS) + random.choice(RANDOM_WORDS)


def generate_random_batch(job_id, batch_size):
    """Generate random Firebase-style URLs for auto-random job."""
    try:
        c = db()
        existing = {r["url"] for r in c.execute(
            "SELECT url FROM items WHERE job_id=?", (job_id,)).fetchall()}
        c.close()

        new_items, attempts = [], 0
        max_attempts = batch_size * 10
        while len(new_items) < batch_size and attempts < max_attempts:
            attempts += 1
            pid = random_project_id()
            u = f"https://{pid}-default-rtdb.firebaseio.com"
            if u in existing: continue
            existing.add(u)
            new_items.append((job_id, u))

        if new_items:
            c = db()
            c.executemany("INSERT INTO items(job_id,url) VALUES(?,?)", new_items)
            c.execute("UPDATE jobs SET total=total+?, updated_at=? WHERE id=?",
                      (len(new_items), now(), job_id))
            c.close()
        return len(new_items)
    except Exception as e:
        log(f"generate_random_batch error: {e}")
        return 0


# ═══════════════════════════════════════════════════════════════
#  AUTO MUTATOR (SEED-BASED)
# ═══════════════════════════════════════════════════════════════
def sanitize_seed(s):
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9\-_]", "", s)
    return s[:60]


def is_valid_mutation(m):
    if not m: return False
    if len(m) > AUTO_MAX_LEN: return False
    parts = m.split("-")
    if len(parts) > AUTO_MAX_PARTS: return False
    if any(p == "" for p in parts): return False
    if sum(c.isdigit() for c in m) > 2: return False
    return True


def mutate_word_smart(word):
    out = {word}
    for p in PREFIXES: out.add(f"{p}{word}")
    for s in SUFFIXES_CLEAN: out.add(f"{word}{s}")
    if "panel" in word:
        base = word.replace("panel", "")
        for ms in MISSPELLS:
            out.add(f"{base}{ms}")
            out.add(f"{base}-{ms}")
    for _ in range(8):
        p = random.choice(PREFIXES)
        s = random.choice(SUFFIXES_CLEAN)
        out.add(f"{p}{word}{s}")
    return [m for m in out if is_valid_mutation(m)]


def extract_project(url):
    try: host = (urlparse(url).hostname or "").lower()
    except Exception: return ""
    for fs in FB_SUFFIXES:
        if host.endswith(fs):
            base = host[:-len(fs)]
            if base.endswith("-default"): base = base[:-len("-default")]
            return base
    return host


def generate_auto_batch(job_id, seeds, batch_size):
    try:
        c = db()
        existing = {r["url"] for r in c.execute(
            "SELECT url FROM items WHERE job_id=?", (job_id,)).fetchall()}
        active_projects = []
        if AUTO_EXPAND:
            for r in c.execute(
                "SELECT url FROM items WHERE job_id=? AND status='active' LIMIT 500",
                (job_id,)).fetchall():
                p = extract_project(r["url"])
                if p and p not in active_projects:
                    active_projects.append(p)
        c.close()

        pool = list(seeds) + active_projects
        if not pool: return 0
        weighted = list(seeds) + active_projects * 3

        new_items, attempts = [], 0
        max_attempts = batch_size * 20
        while len(new_items) < batch_size and attempts < max_attempts:
            attempts += 1
            base = random.choice(weighted)
            mutations = mutate_word_smart(base)
            random.shuffle(mutations)
            for m in mutations:
                for fs in FB_SUFFIXES:
                    u = f"https://{m}{fs}"
                    if u in existing: continue
                    existing.add(u)
                    new_items.append((job_id, u))
                    if len(new_items) >= batch_size: break
                if len(new_items) >= batch_size: break

        if new_items:
            c = db()
            c.executemany("INSERT INTO items(job_id,url) VALUES(?,?)", new_items)
            c.execute("UPDATE jobs SET total=total+?, updated_at=? WHERE id=?",
                      (len(new_items), now(), job_id))
            c.close()
        return len(new_items)
    except Exception as e:
        log(f"generate_auto_batch error: {e}")
        return 0


# ═══════════════════════════════════════════════════════════════
#  JOB CLAIM / RESULT
# ═══════════════════════════════════════════════════════════════
def claim_job():
    c = db()
    try:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT id FROM jobs WHERE status='pending' ORDER BY id LIMIT 1").fetchone()
        if not row: c.execute("ROLLBACK"); return None
        c.execute("UPDATE jobs SET status='processing', started_at=COALESCE(started_at,?), "
                  "updated_at=? WHERE id=? AND status='pending'", (now(), now(), row["id"]))
        if c.total_changes != 1:
            c.execute("ROLLBACK"); return None
        c.execute("COMMIT")
        return row["id"]
    finally:
        c.close()


def claim_item(job_id):
    c = db()
    try:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT * FROM items WHERE job_id=? AND status='pending' "
                        "ORDER BY id LIMIT 1", (job_id,)).fetchone()
        if not row: c.execute("ROLLBACK"); return None
        c.execute("UPDATE items SET status='checking' WHERE id=? AND status='pending'", (row["id"],))
        if c.total_changes != 1:
            c.execute("ROLLBACK"); return None
        c.execute("COMMIT")
        return row
    finally:
        c.close()


def save_result(item, result):
    status, code, detail, latency = result
    c = db()
    try:
        c.execute("BEGIN")
        c.execute("UPDATE items SET status=?,http_status=?,error=?,latency_ms=?,checked_at=? WHERE id=?",
                  (status, code, detail, latency, now(), item["id"]))
        col = {"active": "active", "inactive": "inactive",
               "invalid_no_data": "no_data", "unreachable": "unreachable",
               "response_too_large": "too_large"}.get(status)
        if col:
            c.execute(f"UPDATE jobs SET processed=processed+1,{col}={col}+1,"
                      f"updated_at=? WHERE id=?", (now(), item["job_id"]))
        c.execute("COMMIT")
    except Exception as e:
        log(f"save_result error: {e}")
    finally:
        c.close()

    project = extract_project(item["url"])
    if project: record_seed_attempt(project, status == "active")

    if status == "active":
        is_new_active = persist_active(item["url"], item["job_id"], code or 200, latency)
        device_count = verify_devices(item["url"])
        if device_count > 0:
            is_new_verified = persist_verified(item["url"], item["job_id"],
                                               device_count, code or 200, latency)
            if is_new_verified:
                log(f"✅ VERIFIED #{item['job_id']} | {item['url']} | devices={device_count}")
                tg_notify_verified_new(item["url"], code or 200, latency, device_count)
                tg_notify_verified_count("✅ New verified URL")
        if is_new_active:
            log(f"🎯 ACTIVE #{item['job_id']} | {item['url']} | devices={device_count}")
        else:
            log(f"⚡ ACTIVE (dup) | {item['url']} | devices={device_count}")
    else:
        log(f"JOB #{item['job_id']} → {status.upper()} | {item['url']} | HTTP {code or '-'}")


def finish_if_done(job_id):
    c = db()
    try:
        pending = c.execute(
            "SELECT COUNT(*) n FROM items WHERE job_id=? AND status IN ('pending','checking')",
            (job_id,)).fetchone()["n"]
        if pending == 0:
            c.execute("UPDATE jobs SET status='completed',completed_at=?,updated_at=? "
                      "WHERE id=? AND status='processing'", (now(), now(), job_id))
            return True
        return False
    finally:
        c.close()


def find_running_auto_job():
    c = db()
    row = c.execute("SELECT id FROM jobs WHERE status='processing' AND "
                    "job_type IN ('auto','auto-random') ORDER BY id LIMIT 1").fetchone()
    c.close()
    return row["id"] if row else None


# ═══════════════════════════════════════════════════════════════
#  WORKERS
# ═══════════════════════════════════════════════════════════════
def process_manual_job(job_id):
    log(f"▶ JOB #{job_id} STARTED (manual)")
    while not STOP.is_set():
        item = claim_item(job_id)
        if not item:
            if finish_if_done(job_id):
                log(f"✔ JOB #{job_id} COMPLETED")
                save_all_files()
                with VERIFIED_LOCK: v = len(VERIFIED_URLS)
                tg_notify_verified_count(f"✔ Job #{job_id} done · {v} verified")
                return
            time.sleep(.05); continue
        save_result(item, probe(item["url"]))


def process_auto_tick(job_id):
    item = claim_item(job_id)
    if item:
        save_result(item, probe(item["url"]))
        return True

    with GEN_LOCK:
        c = db()
        pending = c.execute("SELECT COUNT(*) n FROM items WHERE job_id=? AND status='pending'",
                            (job_id,)).fetchone()["n"]
        job = c.execute("SELECT seeds,total,status,job_type FROM jobs WHERE id=?",
                        (job_id,)).fetchone()
        c.close()

        if not job or job["status"] != "processing": return False
        if pending > 0: return True
        if job["total"] >= AUTO_MAX_URLS:
            c = db()
            c.execute("UPDATE jobs SET status='completed',completed_at=?,updated_at=? WHERE id=?",
                      (now(), now(), job_id))
            c.close()
            log(f"AUTO #{job_id} CAP REACHED"); return False

        jt = job["job_type"] or "auto"

        # RANDOM MODE — no seeds needed
        if jt == "auto-random":
            n = generate_random_batch(job_id, AUTO_BATCH)
            return n > 0

        # SEED MODE — needs seeds
        try: seeds = json.loads(job["seeds"] or "[]")
        except Exception: seeds = []
        if not seeds:
            c = db()
            c.execute("UPDATE jobs SET status='completed',completed_at=?,updated_at=? WHERE id=?",
                      (now(), now(), job_id))
            c.close(); return False

        n = generate_auto_batch(job_id, seeds, AUTO_BATCH)
        return n > 0


def worker(n):
    log(f"⚡ worker #{n} ready")
    while not STOP.is_set():
        jid = claim_job()
        if jid:
            c = db()
            j = c.execute("SELECT job_type FROM jobs WHERE id=?", (jid,)).fetchone()
            c.close()
            jt = j["job_type"] if j else "manual"
            if jt in ("auto", "auto-random"):
                process_auto_tick(jid); continue
            try:
                process_manual_job(jid)
            except Exception as e:
                log(f"JOB #{jid} FAILED: {type(e).__name__}: {e}")
                c = db()
                c.execute("UPDATE jobs SET status='failed',error=?,updated_at=? WHERE id=?",
                          (str(e)[:500], now(), jid))
                c.close()
            continue

        auto_jid = find_running_auto_job()
        if auto_jid:
            if not process_auto_tick(auto_jid):
                time.sleep(0.3)
            continue
        time.sleep(0.3)


# ═══════════════════════════════════════════════════════════════
#  DB READS
# ═══════════════════════════════════════════════════════════════
def get_job(token, include_items=True):
    c = db()
    job = c.execute("SELECT * FROM jobs WHERE token=?", (token,)).fetchone()
    if not job: c.close(); return None
    data = dict(job)
    # Elapsed time
    data["elapsed_seconds"] = elapsed_seconds(job["started_at"], job["completed_at"])
    if include_items:
        rows = c.execute("SELECT url,status,http_status,error,latency_ms,checked_at "
                         "FROM items WHERE job_id=? ORDER BY id", (job["id"],)).fetchall()
        data["items"] = [dict(r) for r in rows]
    c.close()
    return data


def recent_jobs():
    c = db()
    rows = c.execute(
        "SELECT id,token,filename,total,processed,active,inactive,no_data,"
        "unreachable,too_large,status,job_type,created_at,started_at,completed_at,updated_at "
        "FROM jobs ORDER BY id DESC LIMIT 50").fetchall()
    c.close()
    out = []
    for r in rows:
        d = dict(r)
        d["elapsed_seconds"] = elapsed_seconds(r["started_at"], r["completed_at"])
        out.append(d)
    return out


def global_stats():
    c = db()
    jobs = c.execute("SELECT COUNT(*) n FROM jobs").fetchone()["n"]
    items = c.execute(
        "SELECT COUNT(*) total, "
        "SUM(CASE WHEN status='active' THEN 1 ELSE 0 END) active, "
        "SUM(CASE WHEN status='inactive' THEN 1 ELSE 0 END) inactive, "
        "SUM(CASE WHEN status='invalid_no_data' THEN 1 ELSE 0 END) no_data, "
        "SUM(CASE WHEN status='unreachable' THEN 1 ELSE 0 END) unreachable "
        "FROM items").fetchone()
    c.close()
    total = items["total"] or 0
    active = items["active"] or 0
    with ACTIVE_LOCK: a_saved = len(ACTIVE_URLS)
    with VERIFIED_LOCK: v_saved = len(VERIFIED_URLS)
    return {
        "jobs": jobs, "urls_checked": total,
        "active_found": active,
        "inactive_found": items["inactive"] or 0,
        "no_data_found": items["no_data"] or 0,
        "unreachable_found": items["unreachable"] or 0,
        "hit_rate_pct": round(active / total * 100, 2) if total else 0,
        "saved_active": a_saved,
        "saved_verified": v_saved,
    }


# ═══════════════════════════════════════════════════════════════
#  HTTP HANDLER
# ═══════════════════════════════════════════════════════════════
class Handler(BaseHTTPRequestHandler):
    server_version = "FBHunter/4.2"

    def log_message(self, fmt, *args): pass

    def _json(self, obj, status=200):
        raw = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(raw)

    def _text(self, body, filename, mime="text/plain"):
        self.send_response(200)
        self.send_header("Content-Type", f"{mime}; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path

        if path in ("/", "/index.html"):
            fp = BASE / "static" / "index.html"
            if not fp.exists():
                return self._json({"ok": False, "error": "index.html missing"}, 404)
            data = fp.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if path == "/health":
            return self._json({
                "ok": True, "service": "firebase-hunter", "version": "4.2",
                "workers": WORKERS, "auto_max_urls": AUTO_MAX_URLS,
                "auto_batch": AUTO_BATCH, "max_urls": MAX_URLS,
                "telegram": TELEGRAM_ENABLED, "discord": DISCORD_ENABLED,
                "active_count": len(ACTIVE_URLS), "verified_count": len(VERIFIED_URLS),
                "auto_random": True,
            })

        if path == "/api/public-bulk/stats":
            return self._json({"ok": True, "stats": global_stats()})

        if path == "/api/public-bulk/jobs":
            return self._json({"ok": True, "jobs": recent_jobs()})

        if path == "/api/public-bulk/patterns":
            return self._json({"ok": True, "top_seeds": get_top_seeds(50)})

        if path == "/api/public-bulk/active":
            with ACTIVE_LOCK: urls = sorted(ACTIVE_URLS)
            return self._json({"ok": True, "count": len(urls), "urls": urls})

        if path == "/api/public-bulk/active.txt":
            with ACTIVE_LOCK: urls = sorted(ACTIVE_URLS)
            body = "".join(u + "\n" for u in urls).encode("utf-8")
            self._text(body, "active-urls.txt")
            return

        if path == "/api/public-bulk/verified":
            with VERIFIED_LOCK: urls = sorted(VERIFIED_URLS)
            return self._json({"ok": True, "count": len(urls), "urls": urls})

        if path == "/api/public-bulk/verified.txt":
            with VERIFIED_LOCK: urls = sorted(VERIFIED_URLS)
            body = "".join(u + "\n" for u in urls).encode("utf-8")
            self._text(body, "verified-urls.txt")
            return

        if path == "/api/public-bulk/verified.json":
            c = db()
            rows = c.execute("SELECT * FROM verified_urls ORDER BY id DESC").fetchall()
            c.close()
            return self._json({"ok": True, "count": len(rows), "urls": [dict(r) for r in rows]})

        if path == "/api/public-bulk/verified.csv":
            c = db()
            rows = c.execute(
                "SELECT url,device_count,http_status,latency_ms,first_seen_at,source_job_id "
                "FROM verified_urls ORDER BY id DESC").fetchall()
            c.close()
            header = "url,device_count,http_status,latency_ms,first_seen_at,source_job_id\n"
            lines = [header]
            for r in rows:
                lines.append(",".join(
                    f'"{v}"' if v is not None else ""
                    for v in (r["url"], r["device_count"], r["http_status"],
                              r["latency_ms"], r["first_seen_at"], r["source_job_id"])) + "\n")
            self._text("".join(lines).encode("utf-8"), "verified-urls.csv", "text/csv")
            return

        if path.startswith("/api/public-bulk/jobs/"):
            tail = path[len("/api/public-bulk/jobs/"):].strip("/")
            token = tail.split("/", 1)[0] if "/" in tail else tail
            job = get_job(token)
            if not job:
                return self._json({"ok": False, "error": "Job not found"}, 404)
            return self._json({"ok": True, "job": job, "items": job["items"]})

        self._json({"ok": False, "error": "Not found"}, 404)

    def _rate_ok(self, ip):
        with RATE_LOCK:
            ts = [x for x in RATE.get(ip, []) if time.time() - x < RATE_WINDOW]
            if len(ts) >= RATE_LIMIT: return False
            ts.append(time.time()); RATE[ip] = ts
            return True

    def _read_json(self, max_size):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > max_size: return None
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_POST(self):
        path = urlparse(self.path).path
        ip = self.client_address[0]

        if path.startswith("/api/public-bulk/jobs/") and path.endswith("/stop"):
            parts = path.strip("/").split("/")
            if len(parts) != 5:
                return self._json({"ok": False, "error": "Bad path"}, 400)
            token = parts[3]
            c = db()
            cur = c.execute(
                "UPDATE jobs SET status='stopped',completed_at=?,updated_at=? "
                "WHERE token=? AND status IN ('processing','pending')",
                (now(), now(), token))
            changed = cur.rowcount
            c.close()
            if not changed:
                return self._json({"ok": False, "error": "Job not running"}, 404)
            save_all_files()
            log(f"⏹ JOB {token[:8]} STOPPED")
            return self._json({"ok": True})

        # ─── AUTO-RANDOM ENDPOINT (NEW) ───
        if path == "/api/public-bulk/auto-random":
            if not self._rate_ok(ip):
                return self._json({"ok": False, "error": "Rate limit"}, 429)
            try:
                body = self._read_json(50000)
                if body is None: body = {}
            except Exception:
                body = {}

            filename = str(body.get("filename") or f"random-{int(time.time())}")[:180]
            token = secrets.token_urlsafe(24)
            c = db()
            cur = c.execute(
                "INSERT INTO jobs(token,filename,total,status,job_type,seeds) "
                "VALUES(?,?,?,'pending','auto-random',?)",
                (token, filename, 0, json.dumps([])))
            jid = cur.lastrowid
            c.close()
            log(f"🎲 AUTO-RANDOM #{jid} CREATED | {filename}")
            if TELEGRAM_ENABLED:
                tg_notify(f"🎲 <b>Auto Random started</b>\n"
                          f"Job #{jid}\n"
                          f"Mode: Firebase-style random IDs\n"
                          f"Cap: {AUTO_MAX_URLS:,}")
            return self._json({
                "ok": True, "job_id": jid, "token": token,
                "mode": "random", "max_urls": AUTO_MAX_URLS,
            }, 201)

        # ─── AUTO (SEED) ENDPOINT ───
        if path == "/api/public-bulk/auto":
            if not self._rate_ok(ip):
                return self._json({"ok": False, "error": "Rate limit"}, 429)
            try:
                body = self._read_json(50000)
                if body is None:
                    return self._json({"ok": False, "error": "Bad size"}, 413)
            except Exception:
                return self._json({"ok": False, "error": "Invalid JSON"}, 400)

            seeds_raw = str(body.get("seeds", "") or "")
            seeds, seen = [], set()
            for raw in seeds_raw.split(","):
                s = sanitize_seed(raw)
                if s and s not in seen:
                    seen.add(s); seeds.append(s)
                if len(seeds) >= 500: break
            if not seeds:
                return self._json({"ok": False, "error": "No valid seeds"}, 400)

            filename = str(body.get("filename") or "auto-mutator")[:180]
            token = secrets.token_urlsafe(24)
            c = db()
            cur = c.execute(
                "INSERT INTO jobs(token,filename,total,status,job_type,seeds) "
                "VALUES(?,?,?,'pending','auto',?)",
                (token, filename, 0, json.dumps(seeds)))
            jid = cur.lastrowid
            c.close()
            log(f"♻ AUTO #{jid} CREATED | {len(seeds)} seeds")
            if TELEGRAM_ENABLED:
                tg_notify(f"♻️ <b>Auto started</b>\nJob #{jid}\nSeeds: {len(seeds)}\nCap: {AUTO_MAX_URLS:,}")
            return self._json({
                "ok": True, "job_id": jid, "token": token,
                "seeds": len(seeds), "max_urls": AUTO_MAX_URLS,
            }, 201)

        if path != "/api/public-bulk/jobs":
            return self._json({"ok": False, "error": "Not found"}, 404)

        if not self._rate_ok(ip):
            return self._json({"ok": False, "error": "Rate limit"}, 429)
        try:
            body = self._read_json(MAX_BYTES + 20000)
            if body is None:
                return self._json({"ok": False, "error": "Too large"}, 413)
        except Exception:
            return self._json({"ok": False, "error": "Invalid JSON"}, 400)

        content = str(body.get("content", "") or "")
        if len(content.encode()) > MAX_BYTES:
            return self._json({"ok": False, "error": f"Exceeds {MAX_BYTES//1024//1024}MB"}, 413)
        urls = parse_urls(content)
        if not urls:
            return self._json({"ok": False, "error": "No valid URLs"}, 400)

        filename = str(body.get("filename") or "firebase-urls.txt")[:180]
        token = secrets.token_urlsafe(24)
        c = db()
        cur = c.execute(
            "INSERT INTO jobs(token,filename,total,status,job_type) "
            "VALUES(?,?,?,'pending','manual')",
            (token, filename, len(urls)))
        jid = cur.lastrowid
        c.executemany("INSERT INTO items(job_id,url) VALUES(?,?)", [(jid, u) for u in urls])
        c.close()
        log(f"📥 JOB #{jid} CREATED | {len(urls)} URLs")
        if TELEGRAM_ENABLED:
            tg_notify(f"📥 <b>Bulk started</b>\nJob #{jid}\nURLs: {len(urls)}")
        return self._json({
            "ok": True, "job_id": jid, "token": token, "total": len(urls)
        }, 201)


# ═══════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════
def main():
    init_db()
    load_all_persistent()

    for i in range(WORKERS):
        threading.Thread(target=worker, args=(i + 1,), daemon=True).start()

    if TELEGRAM_ENABLED:
        threading.Thread(target=periodic_file_sender, daemon=True).start()

    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    log("━" * 60)
    log("🔥 FIREBASE HUNTER v4.2")
    log("━" * 60)
    log(f"🌐 Server:       http://{HOST}:{PORT}")
    log(f"⚡ Workers:      {WORKERS}")
    log(f"⏱ Timeout:      {TIMEOUT}s")
    log(f"📦 Max URLs:     {MAX_URLS:,}")
    log(f"♻ Auto cap:      {AUTO_MAX_URLS:,}")
    log(f"🎲 Auto-Random:  ENABLED")
    log(f"💾 Active:       {len(ACTIVE_URLS):,} (file only)")
    log(f"✅ Verified:      {len(VERIFIED_URLS):,} (TG)")
    log(f"📢 Telegram:     {'✅ ON' if TELEGRAM_ENABLED else '❌ OFF'}")
    log(f"📢 Discord:      {'✅ ON' if DISCORD_ENABLED else '❌ OFF'}")
    log("━" * 60)

    if TELEGRAM_ENABLED:
        tg_startup_msg()

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log("Shutting down...")
    finally:
        STOP.set()
        save_all_files()
        srv.server_close()
        log("State saved. Bye.")


if __name__ == "__main__":
    main()
