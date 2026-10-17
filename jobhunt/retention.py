"""Retention: keep jobs.db and reports/ bounded to a rolling window.

Why: jobs.db is committed to git on every daily run. At ~0.8 MiB/day of growth it
was 6 MiB short of GitHub's 100 MiB per-file push block on 2026-10-16. Nothing in
the pipeline deleted rows, so the file only ever grew.

Policy (owner ruling 2026-10-16): 30-day retention. A job row older than the window
is deleted unless the owner acted on it (tailored, delivered, or given a tracker
status), since those rows carry application history the tracker must keep.

Dedupe survives the delete: deleted ids go into `pruned_jobs` (id + date only, ~50
bytes each). `db.upsert` treats a tombstoned id as already seen, so a posting that is
still live on a board after 30 days is not re-inserted, re-scored (an LLM call), and
re-digested as "new". Tombstones themselves expire after `tombstone_days`.

Run as a module (`python -m jobhunt.retention`) or call `run()`; the daily workflow
runs it just before persisting state.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import re
from pathlib import Path

import yaml

from . import db

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
_DATE_IN_NAME = re.compile(r"(\d{4}-\d{2}-\d{2})")

TOMBSTONE_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS pruned_jobs ("
    "id TEXT PRIMARY KEY, pruned_at TEXT DEFAULT (datetime('now')))"
)

# A row the owner acted on is never pruned by age.
ACTED_SQL = "(tailored_at IS NOT NULL OR delivered_at IS NOT NULL OR user_status IS NOT NULL)"


def load_policy(config_path: str | Path = ROOT / "config.yaml") -> dict:
    cfg = yaml.safe_load(open(config_path)) or {}
    pol = cfg.get("retention") or {}
    return {
        "enabled": bool(pol.get("enabled", True)),
        "days": int(pol.get("days", 30)),
        "tombstone_days": int(pol.get("tombstone_days", 180)),
    }


def prune_db(con, days: int, tombstone_days: int, today: _dt.date | None = None) -> dict:
    """Apply the window to jobs / digest_log / pruned_jobs. Returns counts. Caller commits."""
    today = today or _dt.date.today()
    cutoff = (today - _dt.timedelta(days=days)).isoformat()
    tomb_cutoff = (today - _dt.timedelta(days=tombstone_days)).isoformat()
    con.execute(TOMBSTONE_SCHEMA)

    victims = f"SELECT id FROM jobs WHERE scraped_at < ? AND NOT {ACTED_SQL}"
    n_tomb = con.execute(
        f"INSERT OR IGNORE INTO pruned_jobs (id, pruned_at) SELECT id, datetime('now') FROM ({victims})",
        (cutoff,),
    ).rowcount
    n_jobs = con.execute(f"DELETE FROM jobs WHERE id IN ({victims})", (cutoff,)).rowcount
    # digest_log rows older than the window are only needed by the tracker's
    # first_seen/last_seen, which no longer has a job row to attach to.
    n_log = con.execute(
        "DELETE FROM digest_log WHERE digest_date < ? AND job_id NOT IN (SELECT id FROM jobs)",
        (cutoff,),
    ).rowcount
    n_tomb_exp = con.execute("DELETE FROM pruned_jobs WHERE pruned_at < ?", (tomb_cutoff,)).rowcount
    return {"cutoff": cutoff, "jobs_deleted": n_jobs, "tombstones_added": n_tomb,
            "digest_log_deleted": n_log, "tombstones_expired": n_tomb_exp}


def prune_reports(days: int, reports_dir: Path = REPORTS, today: _dt.date | None = None) -> list[Path]:
    """Delete report files whose filename date is older than the window."""
    today = today or _dt.date.today()
    cutoff = (today - _dt.timedelta(days=days)).isoformat()
    removed = []
    if not reports_dir.exists():
        return removed
    for p in sorted(reports_dir.iterdir()):
        m = _DATE_IN_NAME.search(p.name)
        if p.is_file() and m and m.group(1) < cutoff:
            p.unlink()
            removed.append(p)
    return removed


def run(config_path: str | Path = ROOT / "config.yaml", con=None, reports_dir: Path = REPORTS) -> dict:
    pol = load_policy(config_path)
    if not pol["enabled"]:
        print("[retention] disabled in config.yaml; skipping")
        return {"enabled": False}
    own = con is None
    con = con or db.connect()
    before = Path(con.execute("PRAGMA database_list").fetchone()[2]).stat().st_size if own else None
    stats = prune_db(con, pol["days"], pol["tombstone_days"])
    con.commit()
    con.execute("VACUUM")
    removed = prune_reports(pol["days"], reports_dir)
    stats["reports_deleted"] = len(removed)
    if own:
        after = Path(con.execute("PRAGMA database_list").fetchone()[2]).stat().st_size
        stats["db_bytes_before"], stats["db_bytes_after"] = before, after
        con.close()
    print(f"[retention] window={pol['days']}d cutoff={stats['cutoff']} "
          f"jobs-{stats['jobs_deleted']} digest_log-{stats['digest_log_deleted']} "
          f"reports-{stats['reports_deleted']} tombstones+{stats['tombstones_added']}"
          + (f" db {before/1048576:.1f}->{stats['db_bytes_after']/1048576:.1f} MiB" if own else ""))
    return stats


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    run(ap.parse_args().config)
