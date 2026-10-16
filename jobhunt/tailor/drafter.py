"""Stage A: Claude call that SELECTS facts and voice from the banks. It never writes a
sentence about the candidate.

Two schemas:
- voice schema (default once profile/voice_bank.json exists): summary, label, position,
  anecdotes, opener and closer are chosen by id; bullets and projects by id; skills by
  group order; the model writes at most two bounded bridge sentences for the cover letter.
- legacy schema (no voice bank, e.g. the public engine with example profiles): the model
  writes a short summary and three cover-letter paragraphs from the facts.

Hard constraints are enforced in code after parsing, not asked for in the prompt:
unknown ids raise, per-role bullet floors are filled from the bank, project counts are
clamped, bridge sentences are capped and must name the company.
"""
import json
import re
from jobhunt import llm

BANNED_PHRASES = (
    "results-driven", "passionate", "leverage", "leveraging", "cutting-edge", "synergy",
    "proven track record", "forward-deployed engineer with", "dynamic ", "seamless",
    "robust solutions", "fast-paced", "i am excited to", "i am confident that",
    "not just", "game-changer", "best-in-class", "world-class", "spearheaded",
)

SYSTEM = """You are assembling, not writing. The candidate, Dexter Domingo, wrote every sentence
in the banks below in his own voice; your job is to pick which of his sentences fit this
posting and in what order. You never paraphrase, never add, never smooth. Where the schema
lets you write (two short bridge sentences in the cover letter) you write plainly, in the
first person as Dexter, about the company and the role only, never about him. Avoid these
phrases anywhere: """ + ", ".join(BANNED_PHRASES) + """.
Return only the JSON object. No preamble, no fences."""

PROMPT = """Select material for a resume and cover letter for the posting below.

Return ONLY valid JSON matching this schema:
{{
  "job_id": "{job_id}",
  "resume": {{
    "summary_id": "one id from SUMMARY CANDIDATES",
    "selected_bullets": ["experience and project bullet ids, in the order they should appear within each role or project"],
    "project_names": ["{min_projects} to {max_projects} project names from PROJECTS, most relevant first"],
    "skill_group_order": ["every skill group name, most relevant first"],
    "keywords_mirrored": ["2 or 3 short exact phrases (1-4 words) copied verbatim from the posting that already appear in the bullets or skills you selected"]
  }},
  "cover_letter": {{
    "opener_id": "one id from OPENERS",
    "anecdote_ids": ["exactly two ids from ANECDOTES, the most relevant stories"],
    "closer_id": "{closer_id}",
    "bridge_sentences": ["0 to 2 sentences, each under 30 words, first person, each naming {company} and the role; they connect the stories to this job and say nothing about the candidate that the stories do not already say"]
  }}
}}

Rules that are checked in code after you answer:
- Every id must exist in the banks. Unknown ids fail the draft.
- Experience: pick at least {recent_floor} bullets for every role dated 2016 or later and at least 1 for older roles when the bank has one; pick more for the roles this posting cares about. Order within a role is yours.
- Projects: {min_projects} to {max_projects} projects; for each chosen project pick 1 to 3 of its bullets. Projects not chosen are omitted entirely.
- Do not pick summary ids outside SUMMARY CANDIDATES (they were pre-filtered for this audience).
- keywords_mirrored are verified against the rendered text: choose ones that are literally present in what you selected.

AUDIENCE for this posting: {audiences}

SUMMARY CANDIDATES (id: text):
{summaries}

EXPERIENCE BULLETS (id (tags): text), grouped by role:
{experience}

PROJECTS (name | audience | context), then their bullets (id (tags): text):
{projects}

SKILL GROUPS:
{skill_groups}

OPENERS (id: text):
{openers}

ANECDOTES (id [themes]: text):
{anecdotes}

JOB POSTING:
Title: {title}
Company: {company}
Location: {location}
Matched requirements (from prior scoring): {matched_reqs}
Gaps (from prior scoring): {gaps}
Description:
{description}
{revision_note}
"""

LEGACY_PROMPT = """You are drafting a tailored resume and cover letter for a specific job posting.

HARD CONSTRAINT: you may only select bullets by their existing "id" from the candidate's
bullet bank below. Never invent, embellish, or add experience not present in the bank.

Return ONLY valid JSON (no markdown fences) matching this schema:
{{
  "job_id": "{job_id}",
  "resume": {{
    "summary": "2-3 sentence summary tailored to this posting, built only from facts below",
    "selected_bullets": ["id", "id", "..."],
    "reordered_skills": ["skill", "..."],
    "keywords_mirrored": ["keyword1", "keyword2", "..."]
  }},
  "cover_letter": {{
    "body_paragraphs": ["para1", "para2", "para3"]
  }}
}}

Guidance:
- selected_bullets: choose 12-18 bullets from the bank whose tags best match this posting's
  requirements; at least two per role dated 2016 or later.
- reordered_skills: reorder (don't invent) the candidate's skill list.
- keywords_mirrored: 2-3 short exact phrases from the posting that literally appear in what you selected.
- cover_letter.body_paragraphs: 3 paragraphs, 220-330 words total, built only from facts below.

CANDIDATE BULLET BANK (id: tags -- text):
{bullet_bank}

CANDIDATE SKILLS:
{skills}

CANDIDATE SUMMARY VARIANTS:
{summary_variants}

JOB POSTING:
Title: {title}
Company: {company}
Location: {location}
Matched requirements (from prior scoring): {matched_reqs}
Gaps (from prior scoring): {gaps}
Description:
{description}
{revision_note}
"""

REVISION_NOTE_TEMPLATE = """
PRIOR ATTEMPT FAILED VERIFICATION -- fix these issues in this revision:
{issues}
"""

RECENT_FLOOR = 2
OLD_FLOOR = 1
RECENT_CUTOFF_YEAR = 2016
MAX_BRIDGE_SENTENCES = 2
MAX_BRIDGE_WORDS = 30
MAX_TOKENS = 8192  # Sonnet 5 emits a thinking block (~6-7K tokens) before the text block


# ---------------------------------------------------------------- bank text helpers

def _experience_text(facts: dict) -> str:
    out = []
    for exp in facts.get("experience", []):
        out.append(f"## {exp.get('title', '')} — {exp.get('company', '')} ({exp.get('start', '')} – {exp.get('end', '')})")
        for b in exp.get("bullets", []):
            out.append(f"{b['id']} ({', '.join(b.get('tags', []))}): {b['text']}")
    return "\n".join(out)


def _projects_text(facts: dict) -> str:
    out = []
    for p in facts.get("projects", []):
        out.append(f"## {p['name']} | {', '.join(p.get('audience', []))} | {p.get('context', '')}")
        for b in p.get("bullets", []):
            out.append(f"{b['id']} ({', '.join(b.get('tags', []))}): {b['text']}")
    return "\n".join(out)


def _bullet_bank_text(facts: dict) -> str:
    lines = []
    for exp in facts.get("experience", []):
        for b in exp.get("bullets", []):
            lines.append(f"{b['id']} ({', '.join(b.get('tags', []))}): {b['text']}")
    for proj in facts.get("projects", []):
        for b in proj.get("bullets", []):
            lines.append(f"{b['id']} [project: {proj['name']}] ({', '.join(b.get('tags', []))}): {b['text']}")
    return "\n".join(lines)


def _ids_text(entries, key="text", extra=None):
    out = []
    for e in entries:
        tag = f" [{', '.join(e.get(extra, []))}]" if extra else ""
        out.append(f"{e['id']}{tag}: {e[key]}")
    return "\n".join(out)


# ---------------------------------------------------------------- model call

def _response_text(msg) -> str:
    kinds = [getattr(b, "type", "?") for b in msg.content]
    usage = getattr(msg, "usage", None)
    print(f"  [drafter] stop_reason={getattr(msg, 'stop_reason', None)} blocks={kinds} "
          f"output_tokens={getattr(usage, 'output_tokens', None)}")
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    if not text.strip():
        raise ValueError(f"model returned no text block (stop_reason="
                         f"{getattr(msg, 'stop_reason', None)}, blocks={kinds})")
    if getattr(msg, "stop_reason", None) == "max_tokens":
        print(f"  [drafter] WARNING: output truncated at max_tokens={MAX_TOKENS}; tail: {text[-200:]!r}")
    return text


def _call_anthropic(model, prompt, system=None):
    client = llm.client()
    kwargs = dict(model=model, max_tokens=MAX_TOKENS,
                  messages=[{"role": "user", "content": prompt}])
    if system:
        kwargs["system"] = system
    return _response_text(client.messages.create(**kwargs))


def _extract_json_object(text):
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


# ---------------------------------------------------------------- enforcement

def _valid_bullet_ids(facts):
    ids = {b["id"] for exp in facts.get("experience", []) for b in exp.get("bullets", [])}
    ids |= {b["id"] for proj in facts.get("projects", []) for b in proj.get("bullets", [])}
    return ids


def _validate_no_fabrication(draft: dict, facts: dict) -> None:
    unknown = [bid for bid in draft.get("resume", {}).get("selected_bullets", [])
               if bid not in _valid_bullet_ids(facts)]
    if unknown:
        raise ValueError(f"drafter fabricated bullet ids not in the bank: {unknown}")


def _role_is_recent(exp: dict) -> bool:
    end = str(exp.get("end", ""))
    if end.lower().startswith("present"):
        return True
    try:
        return int(end[:4]) >= RECENT_CUTOFF_YEAR
    except ValueError:
        return True


def _enforce_floors(draft: dict, facts: dict, style: dict) -> dict:
    """Fill per-role bullet floors from the bank, clamp projects, fix skill group order.
    Mutates and returns the draft. The model chooses; the code guarantees shape."""
    resume = draft.setdefault("resume", {})
    selected = list(dict.fromkeys(resume.get("selected_bullets", [])))

    for exp in facts.get("experience", []):
        bank = [b["id"] for b in exp.get("bullets", [])]
        floor = min(len(bank), RECENT_FLOOR if _role_is_recent(exp) else OLD_FLOOR)
        have = [b for b in selected if b in bank]
        for bid in bank:
            if len(have) >= floor:
                break
            if bid not in have:
                selected.append(bid)
                have.append(bid)

    projects = facts.get("projects", [])
    by_name = {p["name"]: p for p in projects}
    names = [n for n in resume.get("project_names", []) if n in by_name]
    # drop projects the model named but gave no bullets, unless we can fill them
    audiences = set(style.get("audiences", []))
    ranked = sorted(projects, key=lambda p: (0 if set(p.get("audience", [])) & audiences else 1,
                                             p.get("rank", 99)))
    for p in ranked:
        if len(names) >= style.get("min_projects", 2):
            break
        if p["name"] not in names:
            names.append(p["name"])
    names = names[: style.get("max_projects", 4)]
    allowed_project_ids = set()
    for n in names:
        bank = [b["id"] for b in by_name[n].get("bullets", [])]
        have = [b for b in selected if b in bank]
        if not have:
            selected.extend(bank[:2])
            have = bank[:2]
        elif len(have) > 3:
            for extra in have[3:]:
                selected.remove(extra)
        allowed_project_ids |= set(bank)
    # bullets from projects that are not shown would dangle; drop them
    all_project_ids = {b["id"] for p in projects for b in p.get("bullets", [])}
    selected = [b for b in selected if b not in all_project_ids or b in allowed_project_ids]

    resume["selected_bullets"] = selected
    resume["project_names"] = names

    group_names = [g["name"] for g in facts.get("skill_groups", [])]
    order = [g for g in resume.get("skill_group_order", []) if g in group_names]
    order += [g for g in group_names if g not in order]
    resume["skill_group_order"] = order

    kws = [k for k in resume.get("keywords_mirrored", []) if isinstance(k, str) and 0 < len(k) <= 40]
    resume["keywords_mirrored"] = kws[:3]
    return draft


def _validate_voice(draft: dict, facts: dict, style: dict, job: dict) -> None:
    vb = facts.get("voice_bank", {})
    resume = draft.get("resume", {})
    cl = draft.get("cover_letter", {})
    summaries = {s["id"] for s in vb.get("summaries", [])}
    if resume.get("summary_id") not in summaries:
        raise ValueError(f"summary_id {resume.get('summary_id')!r} not in the voice bank")
    if style.get("summary_candidates") and resume["summary_id"] not in style["summary_candidates"]:
        raise ValueError(f"summary_id {resume['summary_id']!r} outside the audience candidates")
    anecdotes = {a["id"] for a in vb.get("anecdotes", [])}
    ids = cl.get("anecdote_ids", [])
    if len(ids) != 2 or len(set(ids)) != 2 or not set(ids) <= anecdotes:
        raise ValueError(f"anecdote_ids must be two distinct bank ids, got {ids}")
    openers = {o["id"] for o in vb.get("openers", [])}
    if cl.get("opener_id") not in openers:
        raise ValueError(f"opener_id {cl.get('opener_id')!r} not in the voice bank")
    closers = {c["id"] for c in vb.get("closers", [])}
    if cl.get("closer_id") not in closers:
        cl["closer_id"] = style.get("closer_id") or next(iter(closers), None)
    bridges = [b.strip() for b in cl.get("bridge_sentences", []) if isinstance(b, str) and b.strip()]
    if len(bridges) > MAX_BRIDGE_SENTENCES:
        raise ValueError(f"more than {MAX_BRIDGE_SENTENCES} bridge sentences")
    company = (job.get("company") or "").lower()
    for b in bridges:
        if len(b.split()) > MAX_BRIDGE_WORDS:
            raise ValueError(f"bridge sentence over {MAX_BRIDGE_WORDS} words: {b!r}")
        if company and company not in b.lower():
            raise ValueError(f"bridge sentence does not name {job.get('company')}: {b!r}")
        low = b.lower()
        hit = next((p for p in BANNED_PHRASES if p in low), None)
        if hit:
            raise ValueError(f"bridge sentence uses a banned phrase ({hit!r}): {b!r}")
    cl["bridge_sentences"] = bridges


# ---------------------------------------------------------------- entry point

def draft(facts: dict, job: dict, model: str, revision_issues: list[str] | None = None,
          style: dict | None = None) -> dict:
    """job: title, company, location, description, matched_reqs, gaps (parsed lists).
    style: from jobhunt.tailor.style.choose(); required for the voice schema."""
    revision_note = ""
    if revision_issues:
        revision_note = REVISION_NOTE_TEMPLATE.format(issues="\n".join(f"- {i}" for i in revision_issues))
    vb = facts.get("voice_bank")
    voice = bool(vb and vb.get("summaries") and vb.get("anecdotes"))

    if voice:
        style = style or {}
        cand = style.get("summary_candidates") or [s["id"] for s in vb["summaries"]]
        summaries = [s for s in vb["summaries"] if s["id"] in cand]
        prompt = PROMPT.format(
            job_id=job["id"], audiences=", ".join(style.get("audiences", ["generalist"])),
            min_projects=style.get("min_projects", 2), max_projects=style.get("max_projects", 4),
            recent_floor=RECENT_FLOOR, closer_id=style.get("closer_id") or vb.get("closers", [{}])[0].get("id", ""),
            summaries=_ids_text(summaries), experience=_experience_text(facts),
            projects=_projects_text(facts),
            skill_groups="\n".join(f"- {g['name']}: {'; '.join(g['items'])}" for g in facts.get("skill_groups", [])),
            openers=_ids_text(vb.get("openers", [])),
            anecdotes=_ids_text(vb.get("anecdotes", []), extra="themes"),
            title=job.get("title", ""), company=job.get("company", ""), location=job.get("location", ""),
            matched_reqs="; ".join(job.get("matched_reqs", [])), gaps="; ".join(job.get("gaps", [])),
            description=(job.get("description") or "")[:9000], revision_note=revision_note,
        )
        system = SYSTEM
    else:
        prompt = LEGACY_PROMPT.format(
            job_id=job["id"], bullet_bank=_bullet_bank_text(facts),
            skills=", ".join(facts.get("skills", [])),
            summary_variants="\n".join(facts.get("summary_variants", [])),
            title=job.get("title", ""), company=job.get("company", ""), location=job.get("location", ""),
            matched_reqs="; ".join(job.get("matched_reqs", [])), gaps="; ".join(job.get("gaps", [])),
            description=(job.get("description") or "")[:9000], revision_note=revision_note,
        )
        system = None

    last_error = None
    for attempt in range(3):
        text = ""
        try:
            text = _call_anthropic(model, prompt, system)
            result = _parse(text)
            _validate_no_fabrication(result, facts)
            if voice:
                result = _enforce_floors(result, facts, style)
                _validate_voice(result, facts, style, job)
                result["style"] = style
                result["schema"] = "voice"
            else:
                result["schema"] = "legacy"
            return result
        except (json.JSONDecodeError, ValueError) as e:
            last_error = e
            print(f"  [drafter] attempt {attempt + 1}/3 failed: {e}; head: {text[:160]!r} tail: {text[-160:]!r}")
    raise last_error
