"""Pluggable renderers. Each module exposes render_resume, render_cover_letter, EXT."""


def get_renderer(fmt: str):
    if fmt == "docx":
        from . import docx_renderer
        return docx_renderer
    if fmt == "pdf":
        from . import reportlab_renderer
        return reportlab_renderer
    raise ValueError(f"unknown tailoring format {fmt!r} (expected 'docx' or 'pdf')")
