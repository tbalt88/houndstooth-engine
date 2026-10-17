import datetime as dt
import sqlite3

from jobhunt import db, retention

TODAY = dt.date(2026, 10, 16)


def _con():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute(db.SCHEMA)
    con.execute(db.DIGEST_LOG_SCHEMA)
    db.migrate_tailor_columns(con)
    return con


def _job(con, jid, scraped, **cols):
    cols = {"id": jid, "title": jid, "company": "c", "location": "l", "scraped_at": scraped, **cols}
    con.execute(f"INSERT INTO jobs ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", list(cols.values()))


def test_prune_deletes_old_unacted_rows_only():
    con = _con()
    _job(con, "old", "2026-08-01 00:00:00", score=40)
    _job(con, "old_tailored", "2026-08-01 00:00:00", score=80, tailored_at="2026-08-02")
    _job(con, "old_status", "2026-08-01 00:00:00", score=72, user_status="Applied")
    _job(con, "fresh", "2026-10-10 00:00:00", score=40)
    con.execute("INSERT INTO digest_log VALUES ('old','2026-08-02'),('old_status','2026-08-02'),('fresh','2026-10-11')")
    s = retention.prune_db(con, days=30, tombstone_days=180, today=TODAY)
    assert s["jobs_deleted"] == 1 and s["tombstones_added"] == 1
    assert {r[0] for r in con.execute("SELECT id FROM jobs")} == {"old_tailored", "old_status", "fresh"}
    # digest_log: only rows that are both old AND orphaned go
    assert {tuple(r) for r in con.execute("SELECT job_id, digest_date FROM digest_log")} == {
        ("old_status", "2026-08-02"), ("fresh", "2026-10-11")}
    assert con.execute("SELECT id FROM pruned_jobs").fetchone()[0] == "old"


def test_tombstone_blocks_reinsert_and_expires():
    con = _con()
    _job(con, db.job_hash("t", "c", "l"), "2026-08-01 00:00:00", score=40)
    retention.prune_db(con, days=30, tombstone_days=180, today=TODAY)
    assert db.upsert(con, {"title": "t", "company": "c", "location": "l", "source": "indeed"}) is False
    assert con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    # once the tombstone expires the posting may come back as new
    con.execute("UPDATE pruned_jobs SET pruned_at='2026-01-01 00:00:00'")
    s = retention.prune_db(con, days=30, tombstone_days=180, today=TODAY)
    assert s["tombstones_expired"] == 1
    assert db.upsert(con, {"title": "t", "company": "c", "location": "l", "source": "indeed"}) is True


def test_upsert_works_before_retention_ever_ran():
    con = _con()  # no pruned_jobs table
    assert db.upsert(con, {"title": "t", "company": "c", "location": "l", "source": "indeed"}) is True


def test_prune_reports_by_filename_date(tmp_path):
    for name in ["digest_2026-08-01.md", "top_fits_2026-08-01.csv", "digest_2026-10-10.md", "notes.txt"]:
        (tmp_path / name).write_text("x")
    removed = retention.prune_reports(30, tmp_path, today=TODAY)
    assert sorted(p.name for p in removed) == ["digest_2026-08-01.md", "top_fits_2026-08-01.csv"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["digest_2026-10-10.md", "notes.txt"]


def test_run_disabled_is_noop(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("retention:\n  enabled: false\n")
    assert retention.run(cfg) == {"enabled": False}


def test_run_end_to_end_on_file_db(tmp_path):
    dbfile = tmp_path / "jobs.db"
    con = sqlite3.connect(dbfile)
    con.row_factory = sqlite3.Row
    con.execute(db.SCHEMA); con.execute(db.DIGEST_LOG_SCHEMA); db.migrate_tailor_columns(con)
    _job(con, "old", "2026-08-01 00:00:00", score=40, description="x" * 100000)
    _job(con, "fresh", "2026-10-15 00:00:00", score=40)
    con.commit()
    rep = tmp_path / "reports"; rep.mkdir(); (rep / "digest_2026-08-01.md").write_text("x")
    cfg = tmp_path / "config.yaml"; cfg.write_text("retention:\n  days: 30\n")
    s = retention.run(cfg, con=con, reports_dir=rep)
    assert s["jobs_deleted"] == 1 and s["reports_deleted"] == 1
    assert con.execute("SELECT id FROM jobs").fetchone()[0] == "fresh"
