#!/usr/bin/env python3
"""Full pipeline: ATS tier -> boards tier -> score -> report.
Usage: python run_all.py [--skip-boards] [--skip-ats] [--min-score 70]
"""
import argparse

from jobhunt import report, scrape_ats, scrape_boards, score

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--skip-boards", action="store_true")
    p.add_argument("--skip-ats", action="store_true")
    p.add_argument("--min-score", type=int, default=70)
    a = p.parse_args()

    if not a.skip_ats:
        scrape_ats.run()
    if not a.skip_boards:
        scrape_boards.run()
    score.run()
    report.run(min_score=a.min_score)
    print("\nDone. Open reports/digest_<today>.md for your ranked list.")
