"""Default PDF renderer: draft JSON (Stage A output) + resume_template.json facts -> PDFs.

reportlab chosen over python-docx/docx2pdf and weasyprint (see MANIFEST §3/§7):
pure-Python, zero non-Python system deps, arm64-native, full layout control.
Single-column, standard fonts, plain-text section headers -- no tables/images/
text-boxes in body content, so ATS parsers read it the same as a human would.
"""
from pathlib import Path

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

EXT = ".pdf"

_styles = getSampleStyleSheet()

NAME_STYLE = ParagraphStyle("Name", parent=_styles["Title"], fontName="Helvetica-Bold",
                            fontSize=18, spaceAfter=2, alignment=0)
CONTACT_STYLE = ParagraphStyle("Contact", parent=_styles["Normal"], fontName="Helvetica",
                               fontSize=9.5, spaceAfter=10, textColor="#333333")
SECTION_STYLE = ParagraphStyle("Section", parent=_styles["Heading2"], fontName="Helvetica-Bold",
                               fontSize=12, spaceBefore=12, spaceAfter=4, textColor="#000000")
BODY_STYLE = ParagraphStyle("Body", parent=_styles["Normal"], fontName="Helvetica",
                            fontSize=10, leading=13, spaceAfter=4)
BULLET_STYLE = ParagraphStyle("Bullet", parent=BODY_STYLE, leftIndent=14, bulletIndent=2,
                              spaceAfter=3)
JOB_HEADER_STYLE = ParagraphStyle("JobHeader", parent=_styles["Normal"], fontName="Helvetica-Bold",
                                  fontSize=10.5, spaceBefore=6, spaceAfter=1)
JOB_DATES_STYLE = ParagraphStyle("JobDates", parent=_styles["Normal"], fontName="Helvetica-Oblique",
                                 fontSize=9.5, spaceAfter=4, textColor="#333333")

MARGIN = 0.7 * inch


def _doc(path: Path) -> SimpleDocTemplate:
    return SimpleDocTemplate(str(path), pagesize=LETTER, topMargin=MARGIN, bottomMargin=MARGIN,
                             leftMargin=MARGIN, rightMargin=MARGIN, title=path.stem)


def _contact_line(contact: dict) -> str:
    parts = [contact.get("location", ""), contact.get("email", ""), contact.get("phone", ""),
             contact.get("linkedin", "")]
    return " | ".join(p for p in parts if p)


def _bullets_by_id(facts: dict) -> dict:
    m = {b["id"]: b["text"] for exp in facts.get("experience", []) for b in exp.get("bullets", [])}
    m.update({b["id"]: b["text"] for proj in facts.get("projects", []) for b in proj.get("bullets", [])})
    return m


def render_resume(facts: dict, draft: dict, out_path: Path) -> Path:
    """facts: resume_template.json content. draft: §5.2 drafter output ('resume' key)."""
    resume = draft["resume"]
    bullets = _bullets_by_id(facts)
    contact = facts["contact"]
    story = [
        Paragraph(contact.get("name", ""), NAME_STYLE),
        Paragraph(_contact_line(contact), CONTACT_STYLE),
        Paragraph("Summary", SECTION_STYLE),
        Paragraph(resume["summary"], BODY_STYLE),
    ]

    story.append(Paragraph("Experience", SECTION_STYLE))
    for exp in facts.get("experience", []):
        header = f"{exp['title']} &mdash; {exp['company']}"
        story.append(Paragraph(header, JOB_HEADER_STYLE))
        story.append(Paragraph(f"{exp['start']} &ndash; {exp['end']}", JOB_DATES_STYLE))
        for bid in resume.get("selected_bullets", []):
            text = bullets.get(bid)
            if text is not None and any(b["id"] == bid for b in exp.get("bullets", [])):
                story.append(Paragraph(f"&bull; {text}", BULLET_STYLE))

    # AI / Agentic Projects: isolated group, rendered only if the drafter picked
    # at least one bullet from it (so non-AI roles omit the section entirely).
    selected = resume.get("selected_bullets", [])
    hit_projects = [p for p in facts.get("projects", [])
                    if any(b["id"] in selected for b in p.get("bullets", []))]
    if hit_projects:
        story.append(Paragraph("AI / Agentic Projects", SECTION_STYLE))
        for proj in hit_projects:
            story.append(Paragraph(proj["name"], JOB_HEADER_STYLE))
            meta_text = " &middot; ".join(x for x in (proj.get("descriptor", ""), proj.get("url", "")) if x)
            if meta_text:
                story.append(Paragraph(meta_text, JOB_DATES_STYLE))
            proj_ids = {b["id"] for b in proj.get("bullets", [])}
            for bid in selected:
                if bid in proj_ids:
                    story.append(Paragraph(f"&bull; {bullets[bid]}", BULLET_STYLE))

    story.append(Paragraph("Skills", SECTION_STYLE))
    story.append(Paragraph(", ".join(resume.get("reordered_skills", [])), BODY_STYLE))

    if facts.get("education"):
        story.append(Paragraph("Education", SECTION_STYLE))
        for ed in facts["education"]:
            line = ed.get("degree", "")
            if ed.get("school"):
                line += f", {ed['school']}"
            if ed.get("year"):
                line += f" ({ed['year']})"
            story.append(Paragraph(line, BODY_STYLE))

    if facts.get("certifications"):
        story.append(Paragraph("Certifications", SECTION_STYLE))
        for c in facts["certifications"]:
            line = c.get("name", "")
            if c.get("issuer"):
                line += f", {c['issuer']}"
            if c.get("year"):
                line += f" ({c['year']})"
            story.append(Paragraph(line, BODY_STYLE))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    _doc(out_path).build(story)
    return out_path


def render_cover_letter(facts: dict, draft: dict, job: dict, out_path: Path) -> Path:
    """job: dict with at least 'title' and 'company' for the header/salutation."""
    contact = facts["contact"]
    paragraphs = draft["cover_letter"]["body_paragraphs"]
    story = [
        Paragraph(contact.get("name", ""), NAME_STYLE),
        Paragraph(_contact_line(contact), CONTACT_STYLE),
        Spacer(1, 12),
        Paragraph(f"Re: {job.get('title', '')} at {job.get('company', '')}", BODY_STYLE),
        Spacer(1, 8),
        Paragraph(f"Dear {job.get('company', 'Hiring Team')} Hiring Team,", BODY_STYLE),
        Spacer(1, 6),
    ]
    for para in paragraphs:
        story.append(Paragraph(para, BODY_STYLE))
        story.append(Spacer(1, 6))
    story.append(Paragraph(f"Sincerely,<br/>{contact.get('name', '')}", BODY_STYLE))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    _doc(out_path).build(story)
    return out_path
