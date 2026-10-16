"""Engine-owned test bootstrap (not synced from the private repo).

The private repo runs its tests against the owner's real profile files. This public
repo ships only the fictional examples (profile/*.example.*), so when no real profile
is present the examples are copied into place before the tests import anything that
reads them. The copies are gitignored, so this never puts data in the repo. If a real
profile/resume_template.json exists, nothing is copied and the tests run against it,
exactly as upstream.
"""
import shutil
from pathlib import Path

PROFILE_DIR = Path(__file__).resolve().parent.parent / "profile"
EXAMPLE_PAIRS = [
    ("resume_template.example.json", "resume_template.json"),
    ("resume_profile.example.md", "resume_profile.md"),
    ("projects.example.json", "projects.json"),
    ("voice_bank.example.json", "voice_bank.json"),
]


def _ensure_profile() -> None:
    if (PROFILE_DIR / "resume_template.json").exists():
        return
    for example, real in EXAMPLE_PAIRS:
        src, dst = PROFILE_DIR / example, PROFILE_DIR / real
        if src.exists() and not dst.exists():
            shutil.copyfile(src, dst)


_ensure_profile()
