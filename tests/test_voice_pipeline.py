"""Track C/D: style layer, voice-schema drafter enforcement, renderer, slop detector,
and an offline end-to-end run against the real profile with a stubbed model."""
import json
from pathlib import Path

import pytest

from jobhunt import profile_load
from jobhunt.tailor import drafter, style, verify
from jobhunt.tailor.renderers import docx_renderer

JOB = {"id": "658613713b7e8ce2", "title": "Senior Applied AI Engineer", "company": "Northwind Medical",
       "location": "San Diego, CA", "description": "Applied AI, LLM, agentic systems, Azure OpenAI, "
       "Dynamics 365 integration in a regulated medical device company.", "matched_reqs": [], "gaps": []}


@pytest.fixture(scope="module")
def facts():
    return profile_load.load_facts()


def test_style_is_deterministic_and_bounded(facts):
    a = style.choose(JOB, facts)
    b = style.choose(JOB, facts)
    assert a == b
    assert a["section_order"][0] == "summary"
    assert set(a["section_order"]) == {"summary", "experience", "projects", "skills", "education"}
    assert a["summary_candidates"]
    assert a["bullet_marker"] in ("•", "–")
    other = style.choose({**JOB, "id": "other-job"}, facts)
    assert other["titles"] != a["titles"] or other["bullet_marker"] != a["bullet_marker"] or other["closer_id"] != a["closer_id"]


def test_audience_inference():
    assert style.infer_audiences({"title": "Dynamics 365 Solution Architect", "description": ""})[0] == "d365-architect"
    assert style.infer_audiences({"title": "Claims Platform Engineer", "description": "payer eligibility"})[0] == "payer-platform"
    assert style.infer_audiences({"title": "Forward Deployed Engineer", "description": ""})[0] == "fde-health-ai"
    assert style.infer_audiences({"title": "Cook", "description": ""}) == ["generalist"]


def _model_answer(facts, st):
    vb = facts["voice_bank"]
    exp_ids = [b["id"] for e in facts["experience"][:2] for b in e["bullets"][:1]]  # deliberately under the floor
    proj = facts["projects"][0]
    return {
        "job_id": JOB["id"],
        "resume": {"summary_id": st["summary_candidates"][0], "selected_bullets": exp_ids + [proj["bullets"][0]["id"]],
                   "project_names": [proj["name"]], "skill_group_order": ["Integration and Cloud"],
                   "keywords_mirrored": ["Kafka", "SOC 2"]},
        "cover_letter": {"opener_id": vb["openers"][0]["id"], "anecdote_ids": [vb["anecdotes"][0]["id"], vb["anecdotes"][1]["id"]],
                         "closer_id": st["closer_id"], "bridge_sentences": ["That is the shape of the work I would expect at Northwind Medical."]},
    }


def test_drafter_enforces_floors_and_projects(facts, monkeypatch):
    st = style.choose(JOB, facts)
    monkeypatch.setattr(drafter, "_call_anthropic", lambda m, p, s=None: json.dumps(_model_answer(facts, st)))
    d = drafter.draft(facts, JOB, model="m", style=st)
    assert d["schema"] == "voice"
    sel = d["resume"]["selected_bullets"]
    for exp in facts["experience"]:
        bank = [b["id"] for b in exp["bullets"]]
        floor = min(len(bank), drafter.RECENT_FLOOR if drafter._role_is_recent(exp) else drafter.OLD_FLOOR)
        assert len([b for b in sel if b in bank]) >= floor, exp["title"]
    assert st["min_projects"] <= len(d["resume"]["project_names"]) <= st["max_projects"]
    names = [g["name"] for g in facts["skill_groups"]]
    assert sorted(d["resume"]["skill_group_order"]) == sorted(names)
    assert d["resume"]["skill_group_order"][0] == "Integration and Cloud"


def test_drafter_rejects_bad_voice_ids(facts, monkeypatch):
    st = style.choose(JOB, facts)
    bad = _model_answer(facts, st)
    bad["cover_letter"]["anecdote_ids"] = ["nope", "nope"]
    monkeypatch.setattr(drafter, "_call_anthropic", lambda m, p, s=None: json.dumps(bad))
    with pytest.raises(ValueError):
        drafter.draft(facts, JOB, model="m", style=st)


def test_drafter_rejects_bridge_without_company_or_with_banned_phrase(facts, monkeypatch):
    st = style.choose(JOB, facts)
    for bridge in ["I would love to bring my skills to your team.", "I am passionate about Northwind Medical's mission."]:
        bad = _model_answer(facts, st)
        bad["cover_letter"]["bridge_sentences"] = [bridge]
        monkeypatch.setattr(drafter, "_call_anthropic", lambda m, p, s=None, b=bad: json.dumps(b))
        with pytest.raises(ValueError):
            drafter.draft(facts, JOB, model="m", style=st)


def test_end_to_end_offline_render_and_verify(facts, monkeypatch, tmp_path):
    st = style.choose(JOB, facts)
    monkeypatch.setattr(drafter, "_call_anthropic", lambda m, p, s=None: json.dumps(_model_answer(facts, st)))
    d = drafter.draft(facts, JOB, model="m", style=st)
    r = docx_renderer.render_resume(facts, d, tmp_path / "resume.docx", style=st)
    c = docx_renderer.render_cover_letter(facts, d, JOB, tmp_path / "cover.docx", style=st)
    paras = docx_renderer.cover_letter_paragraphs(facts, d, JOB)
    result = verify.verify_job(r, c, d["resume"]["keywords_mirrored"], draft=d, facts=facts, cover_paragraphs=paras)
    text = verify.extract_text(r)
    # every role renders with at least a context line or a bullet; no bare role headers
    assert "Example Fintech Co" in text and "Sample Systems Inc" in text
    assert "Northwind Medical" in verify.extract_text(c)
    assert result["resume"]["pass"], result
    assert "banned" not in " ".join(result["slop"])
    # skills rendered as clusters, not a wall
    assert "Integration and Cloud:" in text


def test_slop_detector_flags_uniform_bullets_and_banned_phrases():
    reasons = verify.slop_reasons("I leverage synergy — always — and more —", "", None, None,
                                  cover_paragraphs=["x " * 60, "y " * 60, "z " * 60])
    joined = " ".join(reasons)
    assert "banned" in joined and "em dashes" in joined and "near-equal" in joined


def test_legacy_schema_still_renders(facts, tmp_path):
    ids = [b["id"] for e in facts["experience"][:3] for b in e["bullets"][:2]]
    d = {"job_id": "x", "schema": "legacy",
         "resume": {"summary": "Plain summary.", "selected_bullets": ids, "reordered_skills": facts["skills"][:5], "keywords_mirrored": []},
         "cover_letter": {"body_paragraphs": ["a", "b", "c"]}}
    docx_renderer.render_resume(facts, d, tmp_path / "r.docx")
    docx_renderer.render_cover_letter(facts, d, JOB, tmp_path / "c.docx")
    assert (tmp_path / "r.docx").stat().st_size > 0
