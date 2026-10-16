"""Bounded, seeded style variance -- outside the model, reproducible per job.

Principle (docs/plan-2026-10/09-design-principle-human-first.md): imperfection lives in
voice, rhythm and shape, never in facts. This module decides the SHAPE of a document for
one posting: audience, section order, section titles, which label line and position line
are eligible, bullet marker, closer, and how many projects fit. The model only picks ids
within the candidates this module allows. Seeding by job id means re-rendering the same
job yields the same shape, and two different jobs almost never share one.
"""
import random

AUDIENCES = ("d365-architect", "implementation-architect", "fde-health-ai",
             "solutions-engineer", "payer-platform", "ai-platform-governance", "generalist")

# Keyword rules on title + description, in priority order. First two hits win.
_AUDIENCE_RULES = [
    ("d365-architect", ("dynamics", "power platform", "dataverse", "power apps", "copilot studio",
                        "d365", "business applications", "crm architect")),
    ("payer-platform", ("payer", "payor", "claims", "eligibility", "prior auth", "benefits",
                        "health plan", "rcm", "revenue cycle", "adjudication")),
    ("ai-platform-governance", ("ai platform", "llm platform", "agent platform", "governance",
                                "ai safety", "guardrail", "red team", "evaluation engineer",
                                "applied ai", "agentic", "llm")),
    ("fde-health-ai", ("forward deployed", "forward-deployed", "deployment strategist",
                       "customer engineer", "field engineer")),
    ("solutions-engineer", ("solutions engineer", "solutions architect", "solution architect",
                            "implementation", "pre-sales", "presales", "integration architect")),
    ("implementation-architect", ("implementation architect", "delivery architect",
                                  "technical architect")),
]

_TITLES = {
    "experience": ["Experience", "Where I've worked", "Work history"],
    "projects": ["Systems I built and run, 2025 to 2026", "Selected systems, 2025 to 2026",
                 "Things I built in 2026"],
    "skills": ["Skills", "What I'm for", "Tools and domains"],
    "education": ["Education and certifications"],
}

_EXPERIENCE_FIRST = {"d365-architect", "implementation-architect", "payer-platform",
                     "solutions-engineer", "generalist"}


def infer_audiences(job: dict) -> list[str]:
    """Up to two audience tags for a posting, from title (weighted) and description."""
    title = (job.get("title") or "").lower()
    desc = (job.get("description") or "").lower()[:6000]
    scored = []
    for tag, words in _AUDIENCE_RULES:
        score = sum(3 for w in words if w in title) + sum(1 for w in words if w in desc)
        if score:
            scored.append((score, tag))
    scored.sort(key=lambda t: -t[0])
    tags = [t for _, t in scored[:2]]
    return tags or ["generalist"]


def _eligible(entries: list[dict], audiences: list[str]) -> list[dict]:
    """Entries whose 'audience' list overlaps the job's audiences; all entries when none do."""
    hits = [e for e in entries if set(e.get("audience", [])) & set(audiences)]
    return hits or list(entries)


def choose(job: dict, facts: dict) -> dict:
    """Return the style dict for this job. Deterministic for a given job id."""
    rng = random.Random(f"houndstooth-style:{job.get('id', '')}")
    audiences = infer_audiences(job)
    vb = facts.get("voice_bank") or {}

    primary = audiences[0]
    if primary in _EXPERIENCE_FIRST:
        order = ["summary", "experience", "projects", "skills", "education"]
    else:
        order = ["summary", "projects", "experience", "skills", "education"]

    titles = {k: rng.choice(v) for k, v in _TITLES.items()}
    labels = _eligible(vb.get("labels", []), audiences)
    summaries = _eligible(vb.get("summaries", []), audiences)
    positions = vb.get("positions", [])
    closers = vb.get("closers", [])
    openers = vb.get("openers", [])

    return {
        "audiences": audiences,
        "section_order": order,
        "titles": titles,
        "label_id": rng.choice(labels)["id"] if labels else None,
        "summary_candidates": [s["id"] for s in summaries],
        "position_id": rng.choice(positions)["id"] if positions and rng.random() < 0.8 else None,
        "bullet_marker": rng.choice(["•", "–"]),
        "opener_candidates": [o["id"] for o in openers],
        "closer_id": rng.choice(closers)["id"] if closers else None,
        "max_projects": 4 if primary in _EXPERIENCE_FIRST else 5,
        "min_projects": 2,
        "salutation": rng.choice(["Dear {company} team,", "Hello {company} hiring team,",
                                  "To the {company} team,"]),
        "sign_off": rng.choice(["Thanks for reading,", "Regards,", "Best,"]),
    }
