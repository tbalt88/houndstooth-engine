"""Stage 0 hard filters + Stage 1 keyword prefilter + Stage 2 LLM fit scoring."""
import json
import os
import re
from pathlib import Path

import yaml

from . import db, llm

ROOT = Path(__file__).resolve().parent.parent


def _load_profile():
    return (ROOT / "profile" / "resume_profile.md").read_text(encoding="utf-8")


# Cheap prefilter: a job description must touch the candidate's world at least once
PROFILE_KEYWORDS = [
    "architect", "dynamics", "power platform", "power apps", "power automate",
    "azure", "healthcare", "hipaa", "payer", "payor", "eligibility", "claims",
    "release", "d365", "crm", "integration", "microservice", "engineering manager",
    "principal", "program manager", "copilot", "medical", "health plan", "fhir", "hl7",
]

RUBRIC = """You are an expert technical recruiter. Score this job against the candidate profile.

Return ONLY valid JSON (no markdown fences) with keys:
- score: integer 0-100 overall fit. 85+ = apply immediately; 70-84 = strong; 50-69 = maybe; <50 = skip.
- verdict: one sentence on fit.
- matched_reqs: array of up to 6 job requirements the candidate clearly meets (short phrases).
- gaps: array of up to 4 requirements the candidate lacks or only partially meets.
- dealbreakers: array of hard blockers (required certs/clearances/onsite in wrong city/seniority mismatch), or [].
- targeting_tips: array of 3-5 specific resume-targeting suggestions: which of the candidate's bullets
  to emphasize, exact keywords from THIS posting to mirror for ATS parsing, and reframing advice.

Scoring guidance:
- Weight hard requirements over nice-to-haves. Location: candidate is San Diego, CA; remote OK; hybrid San Diego OK; onsite elsewhere is a dealbreaker unless posting says relocation.
- Be honest about gaps (see candidate's stated weaknesses). Do not inflate scores.

CANDIDATE PROFILE:
{profile}

JOB POSTING:
Title: {title}
Company: {company}
Location: {location}
Description:
{description}
"""


def _call_anthropic(model, prompt):
    client = llm.client()  # one client per process; see jobhunt/llm.py
    msg = client.messages.create(model=model, max_tokens=1200,
                                 messages=[{"role": "user", "content": prompt}])
    return msg.content[0].text


def _call_openai(model, prompt):
    from openai import OpenAI
    client = OpenAI()  # OPENAI_API_KEY env var
    r = client.chat.completions.create(model=model,
                                       messages=[{"role": "user", "content": prompt}])
    return r.choices[0].message.content


def _extract_json_object(text):
    """Return the substring spanning the first complete top-level {...} object,
    ignoring braces inside string literals -- guards against the model appending
    trailing prose/notes after the JSON, which json.loads rejects as 'Extra data'."""
    start = text.index("{")
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    raise ValueError("no complete JSON object found in model output")


def _parse(text):
    text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.M).strip()
    return json.loads(_extract_json_object(text))


def run(config_path="config.yaml", max_jobs=None):
    cfg = yaml.safe_load(open(config_path))
    sc = cfg["scoring"]
    filt = cfg.get("filters", {})
    excl = [k.lower() for k in filt.get("exclude_title_keywords", [])]
    call = _call_anthropic if sc["provider"] == "anthropic" else _call_openai
    profile = _load_profile()

    limit = max_jobs if max_jobs is not None else sc.get("max_jobs_per_run", 150)
    con = db.connect()
    rows = con.execute(
        "SELECT * FROM jobs WHERE score IS NULL ORDER BY tier, date_posted DESC LIMIT ?",
        (limit,),
    ).fetchall()
    print(f"[score] {len(rows)} unscored jobs queued")

    scored = skipped = 0
    for r in rows:
        title = (r["title"] or "").lower()
        desc = (r["description"] or "")

        # Stage 0: hard filters
        if any(k in title for k in excl):
            con.execute("UPDATE jobs SET score=0, verdict='hard-filtered by title' WHERE id=?", (r["id"],))
            skipped += 1
            continue
        if filt.get("min_salary") and r["salary_max"] and r["salary_max"] < filt["min_salary"]:
            con.execute("UPDATE jobs SET score=0, verdict='below salary floor' WHERE id=?", (r["id"],))
            skipped += 1
            continue

        # Stage 1: keyword prefilter (skip LLM cost on clearly irrelevant jobs)
        blob = f"{title} {desc}".lower()
        hits = sum(1 for k in PROFILE_KEYWORDS if k in blob)
        if hits < sc.get("min_keyword_prefilter", 1):
            con.execute("UPDATE jobs SET score=5, verdict='prefilter: no profile overlap' WHERE id=?", (r["id"],))
            skipped += 1
            continue

        # Stage 2: LLM scoring
        prompt = RUBRIC.format(profile=profile, title=r["title"], company=r["company"],
                               location=r["location"], description=desc[:9000] or "(no description — score on title only, cap at 60)")
        try:
            out = _parse(call(sc["model"], prompt))
            con.execute(
                """UPDATE jobs SET score=?, verdict=?, matched_reqs=?, gaps=?, dealbreakers=?,
                   targeting_tips=?, scored_at=datetime('now') WHERE id=?""",
                (int(out.get("score", 0)), out.get("verdict", ""),
                 json.dumps(out.get("matched_reqs", [])), json.dumps(out.get("gaps", [])),
                 json.dumps(out.get("dealbreakers", [])), json.dumps(out.get("targeting_tips", [])),
                 r["id"]),
            )
            scored += 1
            print(f"  {out.get('score'):>3}  {r['company'][:28]:<28} {r['title'][:48]}")
        except Exception as e:
            print(f"  !! scoring failed for {r['id']}: {e}")
        con.commit()

    print(f"[score] done — {scored} scored, {skipped} filtered")
    attempted = len(rows) - skipped
    if attempted and scored == 0:
        # Every LLM call failed (bad credential, outage, parse regression). Abort the run
        # so report/deliver never ship a digest built on nothing -- the 2026-10-07/08 runs
        # went green with zero scored jobs after a 401 on every call.
        raise SystemExit(f"[score] FATAL: {attempted} jobs reached the LLM and 0 scored")


if __name__ == "__main__":
    run()
