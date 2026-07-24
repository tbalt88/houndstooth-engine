"""Tier 1 + Tier 3: multi-board scraping via JobSpy (Indeed, Google Jobs, ZipRecruiter, optional LinkedIn)."""
import math
import time

import yaml

from . import db


def _clean(v):
    try:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return None
    except TypeError:
        pass
    return v


def run(config_path="config.yaml"):
    from jobspy import scrape_jobs  # import here so ATS tier works even if jobspy missing

    cfg = yaml.safe_load(open(config_path))
    sites = list(cfg["sites"])
    if cfg.get("linkedin_enabled"):
        sites.append("linkedin")

    con = db.connect()
    new_total = 0

    for search in cfg["searches"]:
        term, tier = search["term"], search.get("tier", 2)
        for loc in cfg["locations"]:
            print(f"[boards] '{term}' @ {loc} ...")
            try:
                df = scrape_jobs(
                    site_name=sites,
                    search_term=term,
                    google_search_term=f"{term} {cfg.get('google_search_suffix', 'jobs')}",
                    location=loc,
                    results_wanted=cfg.get("results_per_search", 30),
                    hours_old=cfg.get("hours_old", 72),
                    country_indeed=cfg.get("country_indeed", "USA"),
                    is_remote=(loc.lower() == "remote"),
                    proxies=cfg.get("proxies") or None,
                    linkedin_fetch_description=cfg.get("linkedin_enabled", False),
                )
            except Exception as e:
                print(f"  !! scrape failed ({e}); continuing")
                continue

            for _, r in df.iterrows():
                job = {
                    "source": r.get("site"),
                    "search_term": term,
                    "tier": tier,
                    "title": _clean(r.get("title")),
                    "company": _clean(r.get("company")),
                    "location": _clean(r.get("location")) or loc,
                    "is_remote": _clean(r.get("is_remote")),
                    "salary_min": _clean(r.get("min_amount")),
                    "salary_max": _clean(r.get("max_amount")),
                    "date_posted": _clean(r.get("date_posted")),
                    "url": _clean(r.get("job_url")),
                    "description": _clean(r.get("description")),
                }
                if not job["title"] or not job["company"]:
                    continue
                if db.upsert(con, job):
                    new_total += 1
            con.commit()
            time.sleep(2)  # politeness between searches

    print(f"[boards] done — {new_total} new jobs")
    return new_total


if __name__ == "__main__":
    run()
