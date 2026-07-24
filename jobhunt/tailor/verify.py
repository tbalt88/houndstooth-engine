"""Stage C: verify a rendered document is real, readable, ATS-parseable text.

PDF path uses pypdf (pure Python) instead of Poppler's pdftotext -- same job,
no native binary dependency (see MANIFEST §7). DOCX path reads paragraph text
via python-docx.
"""
import json
import re
from pathlib import Path

MIN_CHARS = 200
_MOJIBAKE_RE = re.compile(r"[�\x00-\x08\x0b\x0c\x0e-\x1f]")


def extract_text(doc_path: Path) -> str:
    if doc_path.suffix == ".docx":
        from docx import Document
        doc = Document(str(doc_path))
        return "\n".join(p.text for p in doc.paragraphs)
    from pypdf import PdfReader
    reader = PdfReader(str(doc_path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def verify_document(doc_path: Path, required_keywords: list[str]) -> dict:
    """Returns {'pass': bool, 'reasons': [...], 'char_count': int, 'missing_keywords': [...]}."""
    text = extract_text(doc_path)
    reasons = []

    if len(text.strip()) < MIN_CHARS:
        reasons.append(f"extracted text too short ({len(text.strip())} chars < {MIN_CHARS}) "
                        "-- likely image-only or broken render")

    mojibake_hits = _MOJIBAKE_RE.findall(text)
    if mojibake_hits:
        reasons.append(f"found {len(mojibake_hits)} mojibake/control characters in extracted text")

    text_lower = text.lower()
    missing = [kw for kw in required_keywords if kw.lower() not in text_lower]
    if missing:
        reasons.append(f"missing mirrored keywords: {', '.join(missing)}")

    return {
        "file": str(doc_path),
        "pass": not reasons,
        "reasons": reasons,
        "char_count": len(text.strip()),
        "missing_keywords": missing,
    }


def verify_job(resume_doc: Path, cover_doc: Path, keywords: list[str]) -> dict:
    """Combined verification for a job's resume + cover letter pair."""
    resume_result = verify_document(resume_doc, keywords)
    cover_result = verify_document(cover_doc, [])  # cover letter isn't required to mirror every keyword
    return {
        "pass": resume_result["pass"] and cover_result["pass"],
        "resume": resume_result,
        "cover_letter": cover_result,
    }


def write_verification(result: dict, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2))
    return out_path
