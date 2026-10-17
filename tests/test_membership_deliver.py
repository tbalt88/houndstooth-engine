"""Offline tests for digest_log membership: db.py migration, backfill_digest_log.py,
and the deliver.py diff/_digest_html seams. No network, no send_email, no real jobs.db.

Run: .venv\\Scripts\\python.exe -m pytest tests/test_membership_deliver.py -v
"""
import csv
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jobhunt import db, deliver  # noqa: E402
import backfill_digest_log as backfill_mod  # noqa: E402

CSV_HEADER = ["score", "title", "company", "location", "source", "posted", "salary",
              "url", "verdict", "gaps", "targeting_tips", "status"]


def _write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADER)
        writer.writeheader()
        for row in rows:
            full = {k: "" for k in CSV_HEADER}
            full.update(row)
            writer.writerow(full)


def _make_jobs_db(path, jobs):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, url TEXT, title TEXT, company TEXT, location TEXT)")
    for j in jobs:
        con.execute("INSERT INTO jobs (id, url, title, company, location) VALUES (?,?,?,?,?)",
                    (j["id"], j.get("url", ""), j["title"], j["company"], j.get("location", "")))
    con.commit()
    con.row_factory = sqlite3.Row
    return con


def _row(id_, score, title, company, location, url="https://x/job", drive_links=None):
    return {"id": id_, "score": score, "title": title, "company": company,
            "location": location, "url": url, "drive_links": drive_links}


# ---------------- db.py: connect() / migration ----------------

def test_connect_creates_digest_log_table(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    con = db.connect()
    cols = {r[1] for r in con.execute("PRAGMA table_info(digest_log)").fetchall()}
    assert cols == {"job_id", "digest_date"}
    con.close()


def test_digest_log_pk_dedupes_same_day_rerun(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    con = db.connect()
    con.execute("INSERT OR IGNORE INTO digest_log (job_id, digest_date) VALUES (?, ?)", ("job1", "2026-08-01"))
    con.execute("INSERT OR IGNORE INTO digest_log (job_id, digest_date) VALUES (?, ?)", ("job1", "2026-08-01"))
    con.commit()
    assert con.execute("SELECT COUNT(*) FROM digest_log").fetchone()[0] == 1
    con.close()


def test_connect_adds_user_status_and_user_notes_columns(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    con = db.connect()
    job_cols = {r[1] for r in con.execute("PRAGMA table_info(jobs)").fetchall()}
    assert "user_status" in job_cols
    assert "user_notes" in job_cols
    con.close()


def test_connect_migration_is_idempotent_across_two_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    con1 = db.connect()
    con1.close()
    con2 = db.connect()  # must not raise on the already-migrated db
    job_cols = {r[1] for r in con2.execute("PRAGMA table_info(jobs)").fetchall()}
    assert {"user_status", "user_notes"} <= job_cols
    con2.close()


# ---------------- backfill_digest_log.py ----------------

def test_backfill_url_match_and_hash_fallback(tmp_path):
    hash_id = db.job_hash("Widget Engineer", "Acme", "Remote")
    con = _make_jobs_db(tmp_path / "jobs.db", [
        {"id": "url-match-id", "url": "https://example.com/job1", "title": "T1", "company": "C1", "location": "L1"},
        {"id": hash_id, "url": "", "title": "Widget Engineer", "company": "Acme", "location": "Remote"},
    ])
    con.execute(db.DIGEST_LOG_SCHEMA)
    con.commit()

    reports = tmp_path / "reports"
    reports.mkdir()
    _write_csv(reports / "top_fits_2026-08-01.csv", [
        {"score": "90", "title": "T1", "company": "C1", "location": "L1", "url": "https://example.com/job1"},
        {"score": "80", "title": "Widget Engineer", "company": "Acme", "location": "Remote", "url": ""},
        {"score": "50", "title": "Nope", "company": "Nowhere", "location": "X", "url": ""},
    ])

    matched, total = backfill_mod.backfill(con, reports_dir=reports)
    assert total == 3
    assert matched == 2

    ids = {r["job_id"] for r in con.execute("SELECT job_id FROM digest_log").fetchall()}
    assert ids == {"url-match-id", hash_id}


def test_backfill_idempotent_rerun_no_duplicate_rows(tmp_path):
    con = _make_jobs_db(tmp_path / "jobs.db", [
        {"id": "j1", "url": "https://example.com/a", "title": "A", "company": "AA", "location": "L"},
    ])
    con.execute(db.DIGEST_LOG_SCHEMA)
    con.commit()
    reports = tmp_path / "reports"
    reports.mkdir()
    _write_csv(reports / "top_fits_2026-08-02.csv", [
        {"score": "90", "title": "A", "company": "AA", "location": "L", "url": "https://example.com/a"},
    ])

    first = backfill_mod.backfill(con, reports_dir=reports)
    second = backfill_mod.backfill(con, reports_dir=reports)
    assert first == second == (1, 1)
    assert con.execute("SELECT COUNT(*) FROM digest_log").fetchone()[0] == 1


def test_backfill_exact_string_fallback_recovers_stale_id(tmp_path):
    # id intentionally does NOT equal job_hash(title, company, location) -- simulates a row whose
    # title/company/location text was edited out-of-band after the id was originally assigned
    # (the real-world case found in jobs.db: "Microsoft Dynamics Architect" @ "Cecelia Health").
    stale_id = "stale-id-123"
    title, company, location = "Microsoft Dynamics Architect", "Cecelia Health", "Remote, US"
    assert db.job_hash(title, company, location) != stale_id

    con = _make_jobs_db(tmp_path / "jobs.db", [
        {"id": stale_id, "url": "", "title": title, "company": company, "location": location},
    ])
    con.execute(db.DIGEST_LOG_SCHEMA)
    con.commit()

    reports = tmp_path / "reports"
    reports.mkdir()
    _write_csv(reports / "top_fits_2026-08-05.csv", [
        {"score": "95", "title": title, "company": company, "location": location, "url": ""},
    ])

    matched, total = backfill_mod.backfill(con, reports_dir=reports)
    assert (matched, total) == (1, 1)
    ids = {r["job_id"] for r in con.execute("SELECT job_id FROM digest_log").fetchall()}
    assert ids == {stale_id}


def test_backfill_exact_string_fallback_ambiguous_stays_unmatched(tmp_path):
    # Two rows share the exact same (title, company, location) -- the fallback must not guess.
    con = _make_jobs_db(tmp_path / "jobs.db", [
        {"id": "dup-id-1", "url": "", "title": "Duplicate Title", "company": "DupCo", "location": "Remote"},
        {"id": "dup-id-2", "url": "", "title": "Duplicate Title", "company": "DupCo", "location": "Remote"},
    ])
    con.execute(db.DIGEST_LOG_SCHEMA)
    con.commit()

    reports = tmp_path / "reports"
    reports.mkdir()
    _write_csv(reports / "top_fits_2026-08-06.csv", [
        {"score": "60", "title": "Duplicate Title", "company": "DupCo", "location": "Remote", "url": ""},
    ])

    matched, total = backfill_mod.backfill(con, reports_dir=reports)
    assert (matched, total) == (0, 1)
    assert con.execute("SELECT COUNT(*) FROM digest_log").fetchone()[0] == 0


def test_backfill_groups_by_date_from_filename(tmp_path):
    con = _make_jobs_db(tmp_path / "jobs.db", [
        {"id": "j1", "url": "https://example.com/a", "title": "A", "company": "AA", "location": "L"},
        {"id": "j2", "url": "https://example.com/b", "title": "B", "company": "BB", "location": "L"},
    ])
    con.execute(db.DIGEST_LOG_SCHEMA)
    con.commit()
    reports = tmp_path / "reports"
    reports.mkdir()
    _write_csv(reports / "top_fits_2026-08-01.csv", [
        {"score": "90", "title": "A", "company": "AA", "location": "L", "url": "https://example.com/a"},
    ])
    _write_csv(reports / "top_fits_2026-08-02.csv", [
        {"score": "90", "title": "A", "company": "AA", "location": "L", "url": "https://example.com/a"},
        {"score": "80", "title": "B", "company": "BB", "location": "L", "url": "https://example.com/b"},
    ])
    matched, total = backfill_mod.backfill(con, reports_dir=reports)
    assert (matched, total) == (3, 3)
    rows = {(r["job_id"], r["digest_date"]) for r in con.execute("SELECT job_id, digest_date FROM digest_log")}
    assert rows == {("j1", "2026-08-01"), ("j1", "2026-08-02"), ("j2", "2026-08-02")}


# ---------------- suppression: fetch_top_rows / rescore ----------------

def _connect_with_scored_jobs(tmp_path, monkeypatch, jobs):
    """jobs: (id, score, user_status) tuples inserted into a real migrated schema."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    con = db.connect()
    for id_, score, user_status in jobs:
        con.execute(
            "INSERT INTO jobs (id, title, company, score, user_status) VALUES (?,?,?,?,?)",
            (id_, f"T{id_}", f"C{id_}", score, user_status))
    con.commit()
    return con


def test_fetch_top_rows_excludes_dismissed_and_rejected(tmp_path, monkeypatch):
    con = _connect_with_scored_jobs(tmp_path, monkeypatch, [
        ("a", 90, None), ("b", 92, "Dismissed"), ("c", 85, "Rejected"),
        ("d", 80, "Applied"), ("e", 79, "Interested"), ("f", 60, None)])
    ids = [r["id"] for r in deliver.fetch_top_rows(con, 70)]
    assert ids == ["a", "d", "e"]  # score-desc; suppressed and sub-threshold gone


# ---------------- deliver.py: diff seam ----------------

def test_compute_new_rows_is_exact_set_difference(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "diff.db")
    con = db.connect()
    for job_id, d in [("A", "2026-08-05"), ("B", "2026-08-05"), ("B", "2026-08-06"), ("C", "2026-08-06")]:
        con.execute("INSERT OR IGNORE INTO digest_log (job_id, digest_date) VALUES (?, ?)", (job_id, d))
    con.commit()

    top_rows = [{"id": "B"}, {"id": "C"}, {"id": "D"}]
    new_rows = deliver.compute_new_rows(con, top_rows, "2026-08-07")
    assert [r["id"] for r in new_rows] == ["D"]
    con.close()


def test_compute_new_rows_empty_when_no_prior_date(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "diff2.db")
    con = db.connect()
    top_rows = [{"id": "A"}, {"id": "B"}]
    new_rows = deliver.compute_new_rows(con, top_rows, "2026-08-07")
    assert new_rows == []
    con.close()


# ---------------- deliver.py: _digest_html ----------------

def test_digest_html_new_since_yesterday_and_top_matches_sections():
    top_rows = [
        _row("1", 95, "Senior Eng", "Acme", "Remote"),
        _row("2", 80, "Staff Eng", "Beta", "SF"),
        _row("3", 70, "Eng", "Gamma", "NY"),
    ]
    new_rows = [top_rows[0]]  # only job "1" is new
    html = deliver._digest_html(top_rows, {}, 70, new_rows, summary_min_score=78)

    assert "New since yesterday" in html
    assert "Senior Eng" in html
    assert html.index("New since yesterday") < html.index("Senior Eng")
    assert "Top matches (score &ge; 78)" in html
    top_section = html[html.index("Top matches"):]
    assert "Staff Eng" in top_section  # score 80 >= 78
    # existing table untouched below the new sections
    assert '<th style="padding:6px 10px">Score</th>' in html


def test_digest_html_no_new_jobs_branch():
    top_rows = [_row("1", 95, "Senior Eng", "Acme", "Remote")]
    html = deliver._digest_html(top_rows, {}, 70, new_rows=[], summary_min_score=78)
    assert "No new jobs since yesterday." in html


def test_digest_html_defaulted_args_reproduce_legacy_shape():
    top_rows = [_row("1", 95, "Senior Eng", "Acme", "Remote")]
    html = deliver._digest_html(top_rows, {}, 70)  # new_rows/summary_min_score both defaulted
    assert "No new jobs since yesterday." in html  # new_rows=None treated as empty
    assert "Top matches (score &ge; 78)" in html  # default summary_min_score=78
    assert "Houndstooth digest" in html
    assert "Senior Eng" in html
    assert '<table style="border-collapse:collapse;font-size:14px" border="1" bordercolor="#ddd">' in html
