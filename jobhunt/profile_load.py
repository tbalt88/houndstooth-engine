"""Load the candidate fact bank from its three hand-maintained files.

profile/resume_template.json  contact, summary_variants, experience, skills, education, certs
profile/projects.json         {"projects": [...]}      (optional; overrides any inline projects)
profile/voice_bank.json       labels/summaries/positions/anecdotes/openers/closers (optional)

Split on 2026-10-08 so Dexter can edit the voice and project banks as small files while the
generator code changes land independently. Everything downstream still receives one dict
with the same keys the drafter and renderers always used.
"""
import json
from pathlib import Path

PROFILE_DIR = Path(__file__).resolve().parent.parent / "profile"


def load_facts(profile_dir: Path = PROFILE_DIR) -> dict:
    facts = json.loads((profile_dir / "resume_template.json").read_text())
    projects_path = profile_dir / "projects.json"
    if projects_path.exists():
        facts["projects"] = json.loads(projects_path.read_text()).get("projects", [])
    facts.setdefault("projects", [])
    voice_path = profile_dir / "voice_bank.json"
    if voice_path.exists():
        vb = json.loads(voice_path.read_text())
        facts["voice_bank"] = {k: v for k, v in vb.items() if not k.startswith("_")}
    ids = [b["id"] for e in facts.get("experience", []) for b in e.get("bullets", [])]
    ids += [b["id"] for p in facts["projects"] for b in p.get("bullets", [])]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"duplicate bullet ids across experience/projects: {sorted(dupes)}")
    return facts
