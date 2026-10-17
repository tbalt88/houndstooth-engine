import json
import pytest
from jobhunt import profile_load


def test_load_facts_merges_three_files(tmp_path):
    (tmp_path / "resume_template.json").write_text(json.dumps(
        {"contact": {}, "summary_variants": [], "experience": [{"bullets": [{"id": "e1", "text": "t"}]}],
         "skills": [], "education": [], "certifications": []}))
    (tmp_path / "projects.json").write_text(json.dumps({"_note": "x", "projects": [{"name": "P", "bullets": [{"id": "p1", "text": "t"}]}]}))
    (tmp_path / "voice_bank.json").write_text(json.dumps({"_note": "x", "summaries": [{"id": "S1", "text": "hi"}]}))
    f = profile_load.load_facts(tmp_path)
    assert [p["name"] for p in f["projects"]] == ["P"]
    assert f["voice_bank"] == {"summaries": [{"id": "S1", "text": "hi"}]}
    assert "_note" not in f["voice_bank"]


def test_load_facts_rejects_duplicate_ids(tmp_path):
    (tmp_path / "resume_template.json").write_text(json.dumps(
        {"experience": [{"bullets": [{"id": "x", "text": "t"}]}], "projects": [{"bullets": [{"id": "x", "text": "t"}]}]}))
    with pytest.raises(ValueError):
        profile_load.load_facts(tmp_path)


def test_real_profile_loads():
    f = profile_load.load_facts()
    assert f["projects"] and f["voice_bank"]["summaries"] and f["experience"]
