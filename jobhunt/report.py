"""Generate ranked outputs: top-fits CSV + a readable morning digest."""
import csv
import json
from datetime import date
from pathlib import Path

from . import db

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "reports"


def run(min_score=70):
    OUT.mkdir(exist_ok=True)
    con = db.connect()
    rows = con.execute(
        "SELECT * FROM jobs WHERE score >= ? ORDER BY score DESC, date_posted DESC", (min_score,)
    ).fetchall()

    stamp = date.today().isoformat()
    csv_path = OUT / f"top_fits_{stamp}.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["score", "title", "company", "location", "source", "posted",
                    "salary", "url", "verdict", "gaps", "targeting_tips", "status"])
        for r in rows:
            sal = f"{r['salary_min'] or ''}-{r['salary_max'] or ''}".strip("-")
            w.writerow([r["score"], r["title"], r["company"], r["location"], r["source"],
                        r["date_posted"], sal, r["url"], r["verdict"],
                        "; ".join(json.loads(r["gaps"] or "[]")),
                        "; ".join(json.loads(r["targeting_tips"] or "[]")), r["status"]])

    md_path = OUT / f"digest_{stamp}.md"
    with open(md_path, "w") as f:
        f.write(f"# Job Digest — {stamp}\n\n{len(rows)} jobs scoring ≥{min_score}\n\n")
        for r in rows[:25]:
            f.write(f"## {r['score']} — {r['title']} @ {r['company']}\n")
            f.write(f"{r['location']} | {r['source']} | posted {r['date_posted']}\n\n")
            f.write(f"{r['url']}\n\n> {r['verdict']}\n\n")
            tips = json.loads(r["targeting_tips"] or "[]")
            if tips:
                f.write("**Resume targeting:**\n")
                for t in tips:
                    f.write(f"- {t}\n")
            dbk = json.loads(r["dealbreakers"] or "[]")
            if dbk:
                f.write(f"\n⚠️ Dealbreakers: {'; '.join(dbk)}\n")
            f.write("\n---\n\n")

    print(f"[report] {len(rows)} top fits -> {csv_path.name}, {md_path.name}")
    return csv_path, md_path


if __name__ == "__main__":
    run()
