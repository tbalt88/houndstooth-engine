"""Drafter response handling: no bare StopIteration, text blocks joined, retries logged."""
import json
import types
import pytest
from jobhunt.tailor import drafter


def _msg(blocks, stop_reason="end_turn"):
    content = [types.SimpleNamespace(type=t, text=x) for t, x in blocks]
    return types.SimpleNamespace(content=content, stop_reason=stop_reason,
                                 usage=types.SimpleNamespace(output_tokens=42))


def test_response_text_joins_text_blocks():
    assert drafter._response_text(_msg([("text", "{\"a\""), ("text", ": 1}")])) == '{"a": 1}'


def test_response_text_raises_valueerror_when_no_text_block():
    with pytest.raises(ValueError) as ei:
        drafter._response_text(_msg([("thinking", "...")], stop_reason="end_turn"))
    assert "no text block" in str(ei.value)


def test_draft_retries_and_surfaces_last_error(monkeypatch):
    calls = []
    def fake_call(model, prompt, system=None):
        calls.append(1)
        return "{\"resume\": {\"selected_bullets\": [\"x1\""   # truncated on purpose
    monkeypatch.setattr(drafter, "_call_anthropic", fake_call)
    facts = {"experience": [], "projects": [], "skills": [], "summary_variants": []}
    job = {"id": "j", "title": "t", "company": "c", "location": "", "description": ""}
    with pytest.raises(ValueError):
        drafter.draft(facts, job, model="m")
    assert len(calls) == 3


def test_draft_rejects_fabricated_ids(monkeypatch):
    good = {"job_id": "j", "resume": {"summary": "s", "selected_bullets": ["nope"],
            "reordered_skills": [], "keywords_mirrored": []},
            "cover_letter": {"body_paragraphs": ["a", "b", "c"]}}
    monkeypatch.setattr(drafter, "_call_anthropic", lambda m, p, s=None: json.dumps(good))
    facts = {"experience": [{"bullets": [{"id": "e1", "text": "t", "tags": []}]}],
             "projects": [], "skills": [], "summary_variants": []}
    job = {"id": "j", "title": "t", "company": "c", "location": "", "description": ""}
    with pytest.raises(ValueError):
        drafter.draft(facts, job, model="m")
