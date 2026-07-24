#!/usr/bin/env python3
"""Phase 2: tailor resume + cover letter PDFs for jobs scoring >= threshold.

Usage:
  python phase2_tailor.py                          # all unprocessed jobs >= config min_score
  python phase2_tailor.py --min-score 80
  python phase2_tailor.py --job-id a1b2c3d4e5f6
  python phase2_tailor.py --render-only --job-id a1b2c3d4e5f6   # re-render from cached draft.json
  python phase2_tailor.py --force                  # re-tailor already-tailored jobs too
"""
import argparse
import json
from datetime import datetime
from pathlib import Path

from jobhunt import db
from jobhunt.tailor import drafter, verify
from jobhunt.tailor.renderers import get_renderer

ROOT = Path(__file__).resolve().parent
FACTS_PATH = ROOT / "profile" / "resume_template.json"
OUT_ROOT = ROOT / "tailored_output"


def _job_dict(row):
    return {
        "id": row["id"],
        "title": row["title"],
        "company": row["company"],
        "location": row["location"],
        "description": row["description"],
        "matched_reqs": json.loads(row["matched_reqs"] or "[]"),
        "gaps": json.loads(row["gaps"] or "[]"),
        "targeting_tips": json.loads(row["targeting_tips"] or "[]"),
    }


def _select_rows(con, args, cfg):
    if args.job_id:
        rows = con.execute("SELECT * FROM jobs WHERE id=?", (args.job_id,)).fetchall()
    else:
        min_score = args.min_score if args.min_score is not None else cfg.get("min_score", 75)
        clause = "score >= ?" if args.force else "score >= ? AND tailored_at IS NULL"
        rows = con.execute(
            f"SELECT * FROM jobs WHERE {clause} ORDER BY score DESC", (min_score,)
        ).fetchall()
    return rows


def tailor_one(facts, job, model, out_dir, render_only, max_retries, renderer):
    out_dir.mkdir(parents=True, exist_ok=True)
    draft_path = out_dir / "draft.json"

    if render_only:
        if not draft_path.exists():
            raise FileNotFoundError(f"--render-only requires a cached draft at {draft_path}")
        draft_json = json.loads(draft_path.read_text())
        result = _render_and_verify(facts, job, draft_json, out_dir, renderer)
        return draft_json, result

    revision_issues = None
    for attempt in range(max_retries + 1):
        draft_json = drafter.draft(facts, job, model=model, revision_issues=revision_issues)
        draft_path.write_text(json.dumps(draft_json, indent=2))
        result = _render_and_verify(facts, job, draft_json, out_dir, renderer)
        if result["pass"]:
            return draft_json, result
        revision_issues = result["resume"]["reasons"] + result["cover_letter"]["reasons"]

    return draft_json, result  # exhausted retries -- caller flags for manual review


def _render_and_verify(facts, job, draft_json, out_dir, renderer):
    resume_path = renderer.render_resume(facts, draft_json, out_dir / f"resume{renderer.EXT}")
    cover_path = renderer.render_cover_letter(facts, draft_json, job, out_dir / f"cover_letter{renderer.EXT}")
    keywords = draft_json["resume"].get("keywords_mirrored", [])
    result = verify.verify_job(resume_path, cover_path, keywords)
    verify.write_verification(result, out_dir / "verification.json")
    return result


def run(min_score=None, job_id=None, render_only=False, force=False, config_path="config.yaml"):
    import yaml
    cfg = yaml.safe_load(open(config_path)).get("tailoring", {})
    model = cfg.get("model", "claude-sonnet-5")
    max_retries = cfg.get("max_retries", 2)
    renderer = get_renderer(cfg.get("format", "docx"))

    facts = json.loads(FACTS_PATH.read_text())
    con = db.connect()

    args = argparse.Namespace(min_score=min_score, job_id=job_id, force=force)
    rows = _select_rows(con, args, cfg)
    print(f"[tailor] {len(rows)} job(s) queued")

    summary = []
    for row in rows:
        job = _job_dict(row)
        out_dir = OUT_ROOT / job["id"]
        try:
            _, result = tailor_one(facts, job, model, out_dir, render_only, max_retries, renderer)
        except Exception as e:
            print(f"  !! tailoring failed for {job['id']}: {e}")
            continue

        status = "pass" if result["pass"] else "needs_manual_review"
        rel_dir = out_dir.relative_to(ROOT)
        con.execute(
            """UPDATE jobs SET tailored_at=?, tailored_resume_path=?, tailored_cover_path=?,
               verification_status=? WHERE id=?""",
            (datetime.now().isoformat(), str(rel_dir / f"resume{renderer.EXT}"),
             str(rel_dir / f"cover_letter{renderer.EXT}"), status, job["id"]),
        )
        con.commit()
        summary.append((job, row["score"], status, out_dir))
        print(f"  {status:>20}  {row['score']:>3}  {job['company'][:28]:<28} {job['title'][:40]}")

    print("\n--- Summary ---")
    print(f"{'Score':>5}  {'Status':<20}  {'Company':<28}  {'Title':<40}  Path")
    for job, score, status, out_dir in summary:
        print(f"{score:>5}  {status:<20}  {job['company'][:28]:<28}  {job['title'][:40]:<40}  {out_dir}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--min-score", type=int, default=None)
    p.add_argument("--job-id", type=str, default=None)
    p.add_argument("--render-only", action="store_true")
    p.add_argument("--force", action="store_true")
    a = p.parse_args()
    run(min_score=a.min_score, job_id=a.job_id, render_only=a.render_only, force=a.force)
