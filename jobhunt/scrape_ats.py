"""Tier 2: direct ATS endpoints — the cleanest, freshest source. No anti-bot."""
import html
import re
import time

import requests
import yaml

from . import db

UA = {"User-Agent": "Mozilla/5.0 (personal job search tool)"}
TAG = re.compile(r"<[^>]+>")


def _strip(s):
    return html.unescape(TAG.sub(" ", s or "")).strip()


def greenhouse(slug):
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    for j in requests.get(url, headers=UA, timeout=30).json().get("jobs", []):
        yield {
            "title": j.get("title"),
            "location": (j.get("location") or {}).get("name", ""),
            "url": j.get("absolute_url"),
            "description": _strip(j.get("content", ""))[:15000],
            "date_posted": j.get("updated_at", "")[:10],
        }


def lever(slug):
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    for j in requests.get(url, headers=UA, timeout=30).json():
        yield {
            "title": j.get("text"),
            "location": (j.get("categories") or {}).get("location", ""),
            "url": j.get("hostedUrl"),
            "description": _strip(j.get("descriptionPlain") or j.get("description", ""))[:15000],
            "date_posted": "",
        }


def ashby(slug):
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
    for j in requests.get(url, headers=UA, timeout=30).json().get("jobs", []):
        yield {
            "title": j.get("title"),
            "location": j.get("location", ""),
            "url": j.get("jobUrl"),
            "description": _strip(j.get("descriptionPlain", ""))[:15000],
            "date_posted": "",
        }


def workday(slug, site):
    """Unofficial Workday CxS JSON endpoint. Tries common data-center hosts."""
    for dc in ("wd1", "wd5", "wd3", "wd2", "wd12"):
        base = f"https://{slug}.{dc}.myworkdayjobs.com/wday/cxs/{slug}/{site}"
        try:
            r = requests.post(f"{base}/jobs", json={"limit": 20, "offset": 0, "searchText": ""},
                              headers={**UA, "Content-Type": "application/json"}, timeout=20)
            if r.status_code != 200:
                continue
            offset, total = 0, r.json().get("total", 0)
            while offset < min(total, 400):
                data = requests.post(f"{base}/jobs",
                                     json={"limit": 20, "offset": offset, "searchText": ""},
                                     headers={**UA, "Content-Type": "application/json"},
                                     timeout=20).json()
                for j in data.get("jobPostings", []):
                    yield {
                        "title": j.get("title"),
                        "location": j.get("locationsText", ""),
                        "url": f"https://{slug}.{dc}.myworkdayjobs.com/en-US/{site}{j.get('externalPath', '')}",
                        "description": j.get("title", ""),  # list endpoint has no body; title-match only
                        "date_posted": j.get("postedOn", ""),
                    }
                offset += 20
            return
        except Exception:
            continue
    print(f"  !! workday tenant '{slug}' not reachable on common hosts — verify tenant/site slug")


FETCHERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby}


def run(config_path="config.yaml"):
    cfg = yaml.safe_load(open(config_path))
    keywords = [k.lower() for k in cfg.get("ats_title_keywords", [])]
    con = db.connect()
    new_total = 0

    for t in cfg.get("ats_targets", []):
        name, typ = t["name"], t["type"]
        print(f"[ats] {name} ({typ}) ...")
        try:
            gen = (workday(t["slug"], t.get("extra", "")) if typ == "workday"
                   else FETCHERS[typ](t["slug"]))
            for j in gen:
                title = (j.get("title") or "").lower()
                if keywords and not any(k in title for k in keywords):
                    continue
                job = {**j, "source": typ, "company": name, "search_term": "ats_direct", "tier": 1}
                if db.upsert(con, job):
                    new_total += 1
            con.commit()
        except Exception as e:
            print(f"  !! {name} failed: {e}")
        time.sleep(1)

    print(f"[ats] done — {new_total} new jobs")
    return new_total


if __name__ == "__main__":
    run()
