"""One-time, idempotent backfill of digest_log from committed reports/top_fits_*.csv files.

Each top_fits_YYYY-MM-DD.csv reflects the same score>=70 query the digest email used on
that date, so replaying them reconstructs digest membership history exactly.

Usage:
    python backfill_digest_log.py                  # operates on the repo jobs.db
    python backfill_digest_log.py --db <scratch db> # operates on a scratch copy instead
"""
import argparse
import csv
import re
import sqlite3
from pathlib import Path

from jobhunt import db

ROOT = Path(__file__).resolve().parent
REPORTS_DIR = ROOT / "reports"
FILENAME_RE = re.compile(r"top_fits_(\d{4}-\d{2}-\d{2})\.csv$")


def _open_scratch(db_path) -> sqlite3.Connection:
    """Open an arbitrary sqlite file directly (no jobs-table creation) and ensure digest_log exists."""
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute(db.DIGEST_LOG_SCHEMA)
    con.commit()
    return con


def match_job_id(con: sqlite3.Connection, row: dict) -> str | None:
    """Match a CSV row to a jobs.id: exact url, else job_hash(title, company, location), else an
    exact (title, company, location) string match -- but only when it identifies exactly one job
    (recovers rows whose stored id predates a later out-of-band edit to their title/company/
    location text; ambiguous or zero matches fall through to unmatched, same as before)."""
    url = (row.get("url") or "").strip()
    if url:
        found = con.execute("SELECT id FROM jobs WHERE url = ?", (url,)).fetchone()
        if found:
            return found["id"]

    title = row.get("title", "")
    company = row.get("company", "")
    location = row.get("location", "")

    job_id = db.job_hash(title, company, location)
    found = con.execute("SELECT id FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if found:
        return job_id

    exact = con.execute(
        "SELECT id FROM jobs WHERE title = ? AND company = ? AND location = ?",
        (title, company, location),
    ).fetchall()
    if len(exact) == 1:
        return exact[0]["id"]

    return None


def backfill(con: sqlite3.Connection, reports_dir: Path = REPORTS_DIR) -> tuple[int, int]:
    """Replay every top_fits_*.csv in reports_dir into digest_log. Returns (total_matched, total_rows)."""
    total_matched = 0
    total_rows = 0
    for csv_path in sorted(reports_dir.glob("top_fits_*.csv")):
        m = FILENAME_RE.search(csv_path.name)
        if not m:
            continue
        digest_date = m.group(1)
        matched = 0
        rows = 0
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows += 1
                job_id = match_job_id(con, row)
                if job_id:
                    con.execute(
                        "INSERT OR IGNORE INTO digest_log (job_id, digest_date) VALUES (?, ?)",
                        (job_id, digest_date),
                    )
                    matched += 1
        con.commit()
        print(f"{digest_date}: {matched}/{rows} matched")
        total_matched += matched
        total_rows += rows
    return total_matched, total_rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=None,
                         help="sqlite db path (default: repo jobs.db via db.DB_PATH)")
    args = parser.parse_args()

    if args.db:
        con = _open_scratch(args.db)
    else:
        con = db.connect()

    total_matched, total_rows = backfill(con)
    con.close()
    print(f"TOTAL: {total_matched}/{total_rows} matched, {total_rows - total_matched} unmatched")


if __name__ == "__main__":
    main()
