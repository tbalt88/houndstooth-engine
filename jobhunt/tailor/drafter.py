"""Stage A: Claude call that selects/reorders facts into the §5.2 draft schema.

Never invents bullets -- only picks ids that already exist in resume_template.json.
Uses Sonnet (not Haiku): this is final-output-quality work, not bulk triage.
"""
import json
import re

PROMPT = """You are drafting a tailored resume and cover letter for a specific job posting.

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
- selected_bullets: choose 10-16 bullets from the bank whose tags best match this posting's
  requirements. Cover the most relevant roles well (3-5 bullets each for the strongest-matching
  roles); it's fine to select fewer or zero bullets for less relevant roles -- the resume still
  shows every role's title and dates for continuity. Within each role, bullets render in the
  order you list them, so lead with the most relevant.
- The bank also includes an "AI / Agentic Projects" group (ids labeled with a [project: ...]
  tag). Select from these when the posting values AI, agentic, LLM, ML, or automation
  experience -- they render in a separate "AI / Agentic Projects" section. For roles with no
  AI/ML angle, select none of them (the section is omitted when nothing from it is selected).
- reordered_skills: reorder (don't invent) the candidate's skill list to foreground what
  this posting asks for.
- keywords_mirrored: 3-6 items, each a SHORT exact phrase (1-4 words, e.g. "Power Platform",
  "Dataverse", "Azure DevOps") copied verbatim from the JOB POSTING text -- never a sentence,
  never multiple keywords joined into one string with commas. Each item must be short enough
  that it will literally appear, word-for-word, somewhere in the summary, skills, or selected
  bullets you write below (these keywords are checked against the rendered resume text
  afterward, so only pick ones you're actually using).
- cover_letter.body_paragraphs: 3 paragraphs, 250-350 words total, referencing the company
  and role by name, built only from facts in the bullet bank / summary variants below.

CANDIDATE BULLET BANK (id: tags -- text):
{bullet_bank}

CANDIDATE SKILLS:
{skills}

CANDIDATE SUMMARY VARIANTS (for inspiration, do not copy verbatim if a better fit exists):
{summary_variants}

JOB POSTING:
Title: {title}
Company: {company}
Location: {location}
Matched requirements (from prior scoring): {matched_reqs}
Gaps (from prior scoring): {gaps}
Targeting tips (from prior scoring): {targeting_tips}
Description:
{description}
{revision_note}
"""

REVISION_NOTE_TEMPLATE = """
PRIOR ATTEMPT FAILED VERIFICATION -- fix these issues in this revision:
{issues}
"""


def _bullet_bank_text(facts: dict) -> str:
    lines = []
    for exp in facts.get("experience", []):
        for b in exp.get("bullets", []):
            lines.append(f"{b['id']} ({', '.join(b.get('tags', []))}): {b['text']}")
    for proj in facts.get("projects", []):
        for b in proj.get("bullets", []):
            lines.append(f"{b['id']} [project: {proj['name']}] "
                         f"({', '.join(b.get('tags', []))}): {b['text']}")
    return "\n".join(lines)


def _call_anthropic(model, prompt):
    import anthropic
    client = anthropic.Anthropic()  # ANTHROPIC_API_KEY env var
    msg = client.messages.create(model=model, max_tokens=4096,
                                 messages=[{"role": "user", "content": prompt}])
    text_block = next(b for b in msg.content if b.type == "text")
    return text_block.text


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


def _validate_no_fabrication(draft: dict, facts: dict) -> None:
    valid_ids = {b["id"] for exp in facts.get("experience", []) for b in exp.get("bullets", [])}
    valid_ids |= {b["id"] for proj in facts.get("projects", []) for b in proj.get("bullets", [])}
    selected = draft.get("resume", {}).get("selected_bullets", [])
    unknown = [bid for bid in selected if bid not in valid_ids]
    if unknown:
        raise ValueError(f"drafter fabricated bullet ids not in resume_template.json: {unknown}")


def draft(facts: dict, job: dict, model: str, revision_issues: list[str] | None = None) -> dict:
    """job: dict with title, company, location, description, matched_reqs, gaps, targeting_tips (already-parsed lists)."""
    revision_note = ""
    if revision_issues:
        revision_note = REVISION_NOTE_TEMPLATE.format(issues="\n".join(f"- {i}" for i in revision_issues))

    prompt = PROMPT.format(
        job_id=job["id"],
        bullet_bank=_bullet_bank_text(facts),
        skills=", ".join(facts.get("skills", [])),
        summary_variants="\n".join(facts.get("summary_variants", [])),
        title=job.get("title", ""),
        company=job.get("company", ""),
        location=job.get("location", ""),
        matched_reqs="; ".join(job.get("matched_reqs", [])),
        gaps="; ".join(job.get("gaps", [])),
        targeting_tips="; ".join(job.get("targeting_tips", [])),
        description=(job.get("description") or "")[:9000],
        revision_note=revision_note,
    )
    last_error = None
    for _ in range(3):  # guards against occasional truncated/malformed JSON from the model
        try:
            result = _parse(_call_anthropic(model, prompt))
            _validate_no_fabrication(result, facts)
            return result
        except (json.JSONDecodeError, ValueError) as e:
            last_error = e
    raise last_error
