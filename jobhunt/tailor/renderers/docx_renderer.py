"""DOCX renderer: draft JSON + facts (+ style) -> .docx files.

ATS-safe rules kept: single column, one standard font, real headings, no images or text
boxes. Added since 2026-10-08 (Track D): a borderless two-cell header table, section rules,
roles grouped under their company with right-tab dates and a grey context line, project
names as hyperlinks, skills as named clusters, and a cover letter assembled from the voice
bank. The legacy draft schema (free summary, flat skills, three paragraphs) still renders.
"""
from pathlib import Path

from docx import Document
from docx.enum.text import WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

EXT = ".docx"

_BODY_FONT = "Calibri"
_GREY = RGBColor(0x44, 0x44, 0x44)
_LIGHT = RGBColor(0x66, 0x66, 0x66)
_RIGHT_TAB = Inches(7.1)

_LEGACY_TITLES = {"experience": "Experience", "projects": "AI / Agentic Projects",
                  "skills": "Skills", "education": "Education and certifications"}


# ---------------------------------------------------------------- primitives

def _new_doc() -> Document:
    doc = Document()
    normal = doc.styles["Normal"]
    normal.font.name = _BODY_FONT
    normal.font.size = Pt(10.5)
    normal.paragraph_format.space_after = Pt(2)
    for section in doc.sections:
        section.top_margin = section.bottom_margin = Inches(0.6)
        section.left_margin = section.right_margin = Inches(0.7)
    return doc


def _bottom_border(paragraph) -> None:
    p_pr = paragraph._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    for k, v in (("w:val", "single"), ("w:sz", "6"), ("w:space", "1"), ("w:color", "999999")):
        bottom.set(qn(k), v)
    borders.append(bottom)
    p_pr.append(borders)


def _hyperlink(paragraph, url: str, text: str, bold=False) -> None:
    part = paragraph.part
    r_id = part.relate_to(url, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
                          is_external=True)
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), r_id)
    run = OxmlElement("w:r")
    r_pr = OxmlElement("w:rPr")
    if bold:
        r_pr.append(OxmlElement("w:b"))
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "1F4E79")
    r_pr.append(color)
    u = OxmlElement("w:u")
    u.set(qn("w:val"), "single")
    r_pr.append(u)
    run.append(r_pr)
    t = OxmlElement("w:t")
    t.text = text
    t.set(qn("xml:space"), "preserve")
    run.append(t)
    link.append(run)
    paragraph._p.append(link)


def _section(doc, title: str) -> None:
    para = doc.add_paragraph()
    para.paragraph_format.space_before = Pt(9)
    para.paragraph_format.space_after = Pt(3)
    run = para.add_run(title.upper())
    run.bold = True
    run.font.size = Pt(10)
    run.font.color.rgb = _GREY
    _bottom_border(para)


def _grey(doc, text: str, size=9.5, italic=True):
    para = doc.add_paragraph()
    run = para.add_run(text)
    run.italic = italic
    run.font.size = Pt(size)
    run.font.color.rgb = _LIGHT
    return para


def _bullet(doc, text: str, marker: str = "•"):
    para = doc.add_paragraph()
    para.paragraph_format.left_indent = Inches(0.22)
    para.paragraph_format.first_line_indent = Inches(-0.17)
    para.paragraph_format.space_after = Pt(1.5)
    para.add_run(f"{marker} {text}")
    return para


def _header(doc, facts: dict, label: str | None) -> None:
    contact = facts["contact"]
    table = doc.add_table(rows=1, cols=2)
    table.autofit = True
    left, right = table.rows[0].cells
    left.width, right.width = Inches(4.6), Inches(2.5)
    p = left.paragraphs[0]
    run = p.add_run(contact.get("name", ""))
    run.bold = True
    run.font.size = Pt(17)
    if label:
        p2 = left.add_paragraph()
        r2 = p2.add_run(label)
        r2.font.size = Pt(10)
        r2.font.color.rgb = _GREY
    lines = [contact.get("location", ""), contact.get("email", ""), contact.get("phone", ""),
             contact.get("linkedin", ""), contact.get("github", "")]
    rp = right.paragraphs[0]
    rp.alignment = 2  # right
    first = True
    for line in [x for x in lines if x]:
        if not first:
            rp.add_run("\n")
        first = False
        r = rp.add_run(line)
        r.font.size = Pt(9)
        r.font.color.rgb = _GREY


def _bullets_by_id(facts: dict) -> dict:
    m = {b["id"]: b["text"] for exp in facts.get("experience", []) for b in exp.get("bullets", [])}
    m.update({b["id"]: b["text"] for proj in facts.get("projects", []) for b in proj.get("bullets", [])})
    return m


def _voice_lookup(facts: dict, kind: str, id_: str | None) -> str | None:
    for e in facts.get("voice_bank", {}).get(kind, []):
        if e.get("id") == id_:
            return e.get("text")
    return None


# ---------------------------------------------------------------- sections

def _render_experience(doc, facts, selected, bullets, marker):
    by_company = []
    for exp in facts.get("experience", []):
        if by_company and by_company[-1][0] == exp.get("company"):
            by_company[-1][1].append(exp)
        else:
            by_company.append((exp.get("company"), [exp]))
    for company, roles in by_company:
        head = doc.add_paragraph()
        head.paragraph_format.space_before = Pt(5)
        head.add_run(company).bold = True
        head.paragraph_format.tab_stops.add_tab_stop(_RIGHT_TAB, WD_TAB_ALIGNMENT.RIGHT)
        span = f"{roles[-1].get('start', '')} – {roles[0].get('end', '')}"
        r = head.add_run(f"\t{span}")
        r.font.color.rgb = _LIGHT
        r.font.size = Pt(9.5)
        for exp in roles:
            role = doc.add_paragraph()
            role.paragraph_format.space_before = Pt(2)
            role.paragraph_format.tab_stops.add_tab_stop(_RIGHT_TAB, WD_TAB_ALIGNMENT.RIGHT)
            rr = role.add_run(exp.get("title", ""))
            rr.italic = True
            if len(roles) > 1:
                d = role.add_run(f"\t{exp.get('start', '')} – {exp.get('end', '')}")
                d.font.size = Pt(9)
                d.font.color.rgb = _LIGHT
            if exp.get("context"):
                _grey(doc, exp["context"], size=9.5)
            ids = [b["id"] for b in exp.get("bullets", [])]
            picked = [b for b in selected if b in ids]
            for bid in picked:
                _bullet(doc, bullets[bid], marker)


def _render_projects(doc, facts, draft, selected, bullets, marker, voice: bool):
    resume = draft["resume"]
    if voice:
        names = resume.get("project_names", [])
        projects = [p for p in facts.get("projects", []) if p["name"] in names]
        projects.sort(key=lambda p: names.index(p["name"]))
    else:
        projects = [p for p in facts.get("projects", [])
                    if any(b["id"] in selected for b in p.get("bullets", []))]
    for proj in projects:
        head = doc.add_paragraph()
        head.paragraph_format.space_before = Pt(4)
        if proj.get("url"):
            _hyperlink(head, proj["url"] if proj["url"].startswith("http") else "https://" + proj["url"],
                       proj["name"], bold=True)
        else:
            head.add_run(proj["name"]).bold = True
        if proj.get("descriptor"):
            d = head.add_run(f"   {proj['descriptor']}")
            d.font.size = Pt(9)
            d.font.color.rgb = _LIGHT
        if proj.get("context"):
            _grey(doc, proj["context"], size=9.5)
        ids = [b["id"] for b in proj.get("bullets", [])]
        for bid in [b for b in selected if b in ids]:
            _bullet(doc, bullets[bid], marker)


def _render_skills(doc, facts, draft, voice: bool):
    resume = draft["resume"]
    if voice and facts.get("skill_groups"):
        groups = {g["name"]: g for g in facts["skill_groups"]}
        for name in resume.get("skill_group_order", list(groups)):
            g = groups.get(name)
            if not g:
                continue
            para = doc.add_paragraph()
            para.paragraph_format.space_after = Pt(1.5)
            para.add_run(f"{g['name']}: ").bold = True
            para.add_run(", ".join(g["items"]))
    else:
        doc.add_paragraph(", ".join(resume.get("reordered_skills", [])))


def _render_education(doc, facts):
    items = []
    for ed in facts.get("education", []):
        line = ed.get("degree", "")
        if ed.get("school"):
            line += f", {ed['school']}"
        if ed.get("year"):
            line += f" ({ed['year']})"
        items.append(line)
    if items:
        doc.add_paragraph(" · ".join(items))
    certs = []
    for c in facts.get("certifications", []):
        line = c.get("name", "")
        if c.get("issuer"):
            line += f", {c['issuer']}"
        if c.get("year"):
            line += f" ({c['year']})"
        certs.append(line)
    if certs:
        doc.add_paragraph(" · ".join(certs))


# ---------------------------------------------------------------- entry points

def render_resume(facts: dict, draft: dict, out_path: Path, style: dict | None = None) -> Path:
    resume = draft["resume"]
    voice = draft.get("schema") == "voice" or "summary_id" in resume
    style = style or draft.get("style") or {}
    bullets = _bullets_by_id(facts)
    selected = resume.get("selected_bullets", [])
    marker = style.get("bullet_marker", "•")
    titles = {**_LEGACY_TITLES, **style.get("titles", {})}
    order = style.get("section_order") or ["summary", "experience", "projects", "skills", "education"]

    doc = _new_doc()
    _header(doc, facts, _voice_lookup(facts, "labels", style.get("label_id")) if voice else None)

    for section in order:
        if section == "summary":
            text = _voice_lookup(facts, "summaries", resume.get("summary_id")) if voice else resume.get("summary", "")
            para = doc.add_paragraph()
            para.paragraph_format.space_before = Pt(8)
            para.add_run(text or "")
            pos = _voice_lookup(facts, "positions", style.get("position_id")) if voice else None
            if pos:
                _grey(doc, pos, size=10, italic=True)
        elif section == "experience":
            _section(doc, titles["experience"])
            _render_experience(doc, facts, selected, bullets, marker)
        elif section == "projects":
            if voice and not resume.get("project_names"):
                continue
            if not voice and not any(b["id"] in selected for p in facts.get("projects", []) for b in p.get("bullets", [])):
                continue
            _section(doc, titles["projects"])
            _render_projects(doc, facts, draft, selected, bullets, marker, voice)
        elif section == "skills":
            _section(doc, titles["skills"])
            _render_skills(doc, facts, draft, voice)
        elif section == "education":
            if facts.get("education") or facts.get("certifications"):
                _section(doc, titles["education"])
                _render_education(doc, facts)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    return out_path


def cover_letter_paragraphs(facts: dict, draft: dict, job: dict) -> list[str]:
    """The cover letter body as a list of paragraphs, assembled from the voice bank
    (voice schema) or taken as written (legacy schema). Shared with verify."""
    cl = draft["cover_letter"]
    if draft.get("schema") != "voice" and "anecdote_ids" not in cl:
        return list(cl.get("body_paragraphs", []))
    fill = {"company": job.get("company", ""), "role": job.get("title", "")}
    paras = []
    opener = _voice_lookup(facts, "openers", cl.get("opener_id"))
    if opener:
        paras.append(opener.format(**fill))
    bridges = list(cl.get("bridge_sentences", []))
    for i, aid in enumerate(cl.get("anecdote_ids", [])):
        text = _voice_lookup(facts, "anecdotes", aid)
        if text:
            paras.append(text)
        if i < len(bridges):
            paras.append(bridges[i])
    closer = _voice_lookup(facts, "closers", cl.get("closer_id"))
    if closer:
        paras.append(closer.format(**fill))
    return paras


def render_cover_letter(facts: dict, draft: dict, job: dict, out_path: Path, style: dict | None = None) -> Path:
    style = style or draft.get("style") or {}
    contact = facts["contact"]
    doc = _new_doc()
    _header(doc, facts, None)
    doc.add_paragraph()
    doc.add_paragraph(f"Re: {job.get('title', '')} at {job.get('company', '')}")
    salutation = style.get("salutation", "Dear {company} team,").format(company=job.get("company", "Hiring"))
    doc.add_paragraph(salutation)
    for para in cover_letter_paragraphs(facts, draft, job):
        p = doc.add_paragraph(para)
        p.paragraph_format.space_after = Pt(6)
    doc.add_paragraph(style.get("sign_off", "Sincerely,"))
    doc.add_paragraph(contact.get("name", ""))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    return out_path
