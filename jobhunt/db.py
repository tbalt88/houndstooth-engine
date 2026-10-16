"""SQLite storage with cross-source dedupe."""
import hashlib
import re
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "jobs.db"

DIGEST_LOG_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS digest_log ("
    "job_id TEXT, digest_date TEXT, PRIMARY KEY (job_id, digest_date))"
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,            -- dedupe hash
    source TEXT,                    -- indeed/google/linkedin/greenhouse/...
    source_rank INTEGER,            -- lower = preferred source (ATS best)
    search_term TEXT,
    tier INTEGER,
    title TEXT,
    company TEXT,
    location TEXT,
    is_remote INTEGER,
    salary_min REAL,
    salary_max REAL,
    date_posted TEXT,
    url TEXT,
    description TEXT,
    scraped_at TEXT DEFAULT (datetime('now')),
    -- scoring
    score INTEGER,
    verdict TEXT,
    matched_reqs TEXT,
    gaps TEXT,
    dealbreakers TEXT,
    targeting_tips TEXT,
    scored_at TEXT,
    -- tracking
    status TEXT DEFAULT 'new'       -- new/reviewed/applied/rejected/interview
);
"""

# Tracker Status values that remove a job from pipeline outputs (digest email,
# tailoring, baseline-change re-scoring). Owner decision 2026-08-07. The tracker
# spreadsheet itself always keeps every row -- suppression is a filter, not a delete.
SUPPRESSED_STATUSES = ("Dismissed", "Rejected")


def not_suppressed_sql(column="user_status"):
    """SQL fragment excluding suppressed rows; bind db.SUPPRESSED_STATUSES after it."""
    placeholders = ",".join("?" * len(SUPPRESSED_STATUSES))
    return f"({column} IS NULL OR {column} NOT IN ({placeholders}))"


# Prefer origin ATS data over aggregator copies when the same job arrives twice
SOURCE_RANK = {
    "greenhouse": 0, "lever": 0, "ashby": 0, "workday": 0,
    "indeed": 1, "google": 2, "zip_recruiter": 3, "linkedin": 4,
}

_norm_re = re.compile(r"[^a-z0-9 ]+")


def _norm(s: str) -> str:
    s = _norm_re.sub("", (s or "").lower())
    # strip noise words that vary between postings of the same job
    for w in ("sr", "senior", "jr", "junior", "remote", "hybrid", "usa", "us", "inc", "llc", "corp"):
        s = re.sub(rf"\b{w}\b", "", s)
    return " ".join(s.split())


def job_hash(title: str, company: str, location: str) -> str:
    key = f"{_norm(title)}|{_norm(company)}|{_norm(location)[:20]}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]


# Phase 2 (tailoring) additive columns -- see MANIFEST.md §6
# delivered_at / drive_links added for the Drive-upload + email-digest stage.
TAILOR_COLUMNS = {
    "tailored_at": "TEXT",
    "tailored_resume_path": "TEXT",
    "tailored_cover_path": "TEXT",
    "verification_status": "TEXT",
    "delivered_at": "TEXT",
    "drive_links": "TEXT",
    "user_status": "TEXT",
    "user_notes": "TEXT",
}


def migrate_tailor_columns(con):
    """Idempotent: SQLite has no ADD COLUMN IF NOT EXISTS, so swallow duplicate-column errors."""
    for col, coltype in TAILOR_COLUMNS.items():
        try:
            con.execute(f"ALTER TABLE jobs ADD COLUMN {col} {coltype}")
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise


def connect():
    con = sqlite3.connect(DB_PATH)
    con.execute(SCHEMA)
    con.execute(DIGEST_LOG_SCHEMA)
    con.row_factory = sqlite3.Row
    migrate_tailor_columns(con)
    return con


def _tombstoned(con, jid: str) -> bool:
    """True if retention deleted this id recently (see jobhunt/retention.py)."""
    try:
        return con.execute("SELECT 1 FROM pruned_jobs WHERE id=?", (jid,)).fetchone() is not None
    except sqlite3.OperationalError:  # table not created yet: retention never ran
        return False


def upsert(con, job: dict) -> bool:
    """Insert job; if duplicate exists, keep the better-ranked source. Returns True if new."""
    jid = job_hash(job.get("title", ""), job.get("company", ""), job.get("location", ""))
    rank = SOURCE_RANK.get(job.get("source", ""), 9)
    row = con.execute("SELECT source_rank FROM jobs WHERE id=?", (jid,)).fetchone()
    if row is None and _tombstoned(con, jid):
        return False  # pruned by retention within the tombstone window: already seen, skip
    if row is None:
        con.execute(
            """INSERT INTO jobs (id, source, source_rank, search_term, tier, title, company,
               location, is_remote, salary_min, salary_max, date_posted, url, description)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (jid, job.get("source"), rank, job.get("search_term"), job.get("tier"),
             job.get("title"), job.get("company"), job.get("location"),
             int(bool(job.get("is_remote"))), job.get("salary_min"), job.get("salary_max"),
             str(job.get("date_posted") or ""), job.get("url"), job.get("description")),
        )
        return True
    if rank < row["source_rank"]:  # better source for same job -> replace core fields
        con.execute(
            """UPDATE jobs SET source=?, source_rank=?, url=?,
               description=COALESCE(NULLIF(?, ''), description) WHERE id=?""",
            (job.get("source"), rank, job.get("url"), job.get("description") or "", jid),
        )
    return False
