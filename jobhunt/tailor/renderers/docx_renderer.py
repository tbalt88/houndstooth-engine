"""DOCX renderer: draft JSON (Stage A output) + resume_template.json facts -> .docx files.

Default renderer since 2026-07: user wants editable output rather than final PDFs.
Same ATS-safe rules as the reportlab renderer: single column, standard font,
plain bold section headers, no tables/text-boxes/images in body content.
"""
from pathlib import Path

from docx import Document
from docx.shared import Pt, RGBColor

EXT = ".docx"

_BODY_FONT = "Calibri"
_GREY = RGBColor(0x33, 0x33, 0x33)


def _new_doc() -> Document:
    doc = Document()
    normal = doc.styles["Normal"]
    normal.font.name = _BODY_FONT
    normal.font.size = Pt(10.5)
    for section in doc.sections:
        section.top_margin = section.bottom_margin = Pt(50)
        section.left_margin = section.right_margin = Pt(50)
    return doc


def _add_name_header(doc: Document, contact: dict) -> None:
    name = doc.add_paragraph()
    run = name.add_run(contact.get("name", ""))
    run.bold = True
    run.font.size = Pt(18)

    line = " | ".join(p for p in (contact.get("location", ""), contact.get("email", ""),
                                  contact.get("phone", ""), contact.get("linkedin", "")) if p)
    para = doc.add_paragraph()
    run = para.add_run(line)
    run.font.size = Pt(9.5)
    run.font.color.rgb = _GREY


def _add_section(doc: Document, title: str) -> None:
    para = doc.add_paragraph()
    para.paragraph_format.space_before = Pt(10)
    para.paragraph_format.space_after = Pt(3)
    run = para.add_run(title)
    run.bold = True
    run.font.size = Pt(12)


def _bullets_by_id(facts: dict) -> dict:
    m = {b["id"]: b["text"] for exp in facts.get("experience", []) for b in exp.get("bullets", [])}
    m.update({b["id"]: b["text"] for proj in facts.get("projects", []) for b in proj.get("bullets", [])})
    return m


def render_resume(facts: dict, draft: dict, out_path: Path) -> Path:
    """facts: resume_template.json content. draft: §5.2 drafter output ('resume' key)."""
    resume = draft["resume"]
    bullets = _bullets_by_id(facts)
    doc = _new_doc()

    _add_name_header(doc, facts["contact"])

    _add_section(doc, "Summary")
    doc.add_paragraph(resume["summary"])

    _add_section(doc, "Experience")
    for exp in facts.get("experience", []):
        header = doc.add_paragraph()
        run = header.add_run(f"{exp['title']} — {exp['company']}")
        run.bold = True
        dates = doc.add_paragraph()
        run = dates.add_run(f"{exp['start']} – {exp['end']}")
        run.italic = True
        run.font.size = Pt(9.5)
        run.font.color.rgb = _GREY
        exp_ids = {b["id"] for b in exp.get("bullets", [])}
        for bid in resume.get("selected_bullets", []):
            if bid in exp_ids:
                doc.add_paragraph(bullets[bid], style="List Bullet")

    # AI / Agentic Projects: isolated group, rendered only if the drafter picked
    # at least one bullet from it (so non-AI roles omit the section entirely).
    selected = resume.get("selected_bullets", [])
    hit_projects = [p for p in facts.get("projects", [])
                    if any(b["id"] in selected for b in p.get("bullets", []))]
    if hit_projects:
        _add_section(doc, "AI / Agentic Projects")
        for proj in hit_projects:
            header = doc.add_paragraph()
            header.add_run(proj["name"]).bold = True
            meta_text = " · ".join(x for x in (proj.get("descriptor", ""), proj.get("url", "")) if x)
            if meta_text:
                meta = doc.add_paragraph()
                run = meta.add_run(meta_text)
                run.italic = True
                run.font.size = Pt(9.5)
                run.font.color.rgb = _GREY
            proj_ids = {b["id"] for b in proj.get("bullets", [])}
            for bid in selected:
                if bid in proj_ids:
                    doc.add_paragraph(bullets[bid], style="List Bullet")

    _add_section(doc, "Skills")
    doc.add_paragraph(", ".join(resume.get("reordered_skills", [])))

    if facts.get("education"):
        _add_section(doc, "Education")
        for ed in facts["education"]:
            line = ed.get("degree", "")
            if ed.get("school"):
                line += f", {ed['school']}"
            if ed.get("year"):
                line += f" ({ed['year']})"
            doc.add_paragraph(line)

    if facts.get("certifications"):
        _add_section(doc, "Certifications")
        for c in facts["certifications"]:
            line = c.get("name", "")
            if c.get("issuer"):
                line += f", {c['issuer']}"
            if c.get("year"):
                line += f" ({c['year']})"
            doc.add_paragraph(line)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    return out_path


def render_cover_letter(facts: dict, draft: dict, job: dict, out_path: Path) -> Path:
    """job: dict with at least 'title' and 'company' for the header/salutation."""
    contact = facts["contact"]
    doc = _new_doc()

    _add_name_header(doc, contact)
    doc.add_paragraph()
    doc.add_paragraph(f"Re: {job.get('title', '')} at {job.get('company', '')}")
    doc.add_paragraph(f"Dear {job.get('company', 'Hiring Team')} Hiring Team,")
    for para in draft["cover_letter"]["body_paragraphs"]:
        doc.add_paragraph(para)
    doc.add_paragraph("Sincerely,")
    doc.add_paragraph(contact.get("name", ""))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    return out_path
