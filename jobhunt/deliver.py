"""Delivery stage: upload tailored docs to Google Drive, email an HTML digest with live links.

Auth:
- Drive: OAuth refresh token, authenticated as your own Google account -- NOT a service
  account. Service accounts have no storage quota on personal (non-Workspace) Gmail and
  can't own files there; only Shared Drives (a Workspace-only feature) get around that.
  Run gdrive_authorize.py once locally to mint GDRIVE_CLIENT_ID / GDRIVE_CLIENT_SECRET /
  GDRIVE_REFRESH_TOKEN, then set them as env vars (or GitHub secrets in CI). Uploads count
  against your normal account quota and land in a folder you already own.
- Email: Gmail SMTP with an app password (env GMAIL_APP_PASSWORD).
"""
import json
import os
import smtplib
import ssl
from datetime import date, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import yaml

from . import db

ROOT = Path(__file__).resolve().parent.parent
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


# ---------------- Google Drive ----------------

def _drive_service():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    required = ["GDRIVE_CLIENT_ID", "GDRIVE_CLIENT_SECRET", "GDRIVE_REFRESH_TOKEN"]
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        raise RuntimeError(f"missing env var(s) {missing} -- run gdrive_authorize.py once "
                           "to obtain them (see jobhunt/deliver.py docstring)")
    creds = Credentials(
        token=None,
        refresh_token=os.environ["GDRIVE_REFRESH_TOKEN"],
        client_id=os.environ["GDRIVE_CLIENT_ID"],
        client_secret=os.environ["GDRIVE_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/drive"],
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _safe_name(s: str) -> str:
    return "".join(c for c in (s or "") if c.isalnum() or c in " -_.,()").strip()


def upload_job_docs(service, folder_id: str, job_row) -> dict:
    """Upload resume + cover letter for one job. Returns {'resume': url, 'cover_letter': url}."""
    from googleapiclient.http import MediaFileUpload

    label = f"{_safe_name(job_row['company'])} - {_safe_name(job_row['title'])[:60]}"
    links = {}
    for kind, path_col in (("resume", "tailored_resume_path"), ("cover_letter", "tailored_cover_path")):
        local = ROOT / job_row[path_col]  # tailored_*_path is stored relative to repo root
        if not local.exists():
            raise FileNotFoundError(f"{kind} missing at {local}")
        meta = {"name": f"{label} - {kind}{local.suffix}", "parents": [folder_id]}
        media = MediaFileUpload(str(local), mimetype=DOCX_MIME if local.suffix == ".docx" else None)
        created = service.files().create(body=meta, media_body=media,
                                         fields="id, webViewLink",
                                         supportsAllDrives=True).execute()
        links[kind] = created["webViewLink"]
    return links


# ---------------- Digest email ----------------

def _digest_html(top_rows, tailored_links: dict, min_score: int) -> str:
    stamp = date.today().isoformat()
    rows_html = []
    for r in top_rows:
        links = tailored_links.get(r["id"], {})
        if links:
            docs = (f'<a href="{links["resume"]}">resume</a> &middot; '
                    f'<a href="{links["cover_letter"]}">cover letter</a>')
        elif r["drive_links"]:
            prior = json.loads(r["drive_links"])
            docs = (f'<a href="{prior["resume"]}">resume</a> &middot; '
                    f'<a href="{prior["cover_letter"]}">cover letter</a>')
        else:
            docs = "&mdash;"
        rows_html.append(f"""
        <tr>
          <td style="padding:6px 10px;text-align:center;font-weight:bold">{r['score']}</td>
          <td style="padding:6px 10px"><a href="{r['url']}">{r['title']}</a></td>
          <td style="padding:6px 10px">{r['company']}</td>
          <td style="padding:6px 10px">{r['location'] or ''}</td>
          <td style="padding:6px 10px">{docs}</td>
        </tr>""")
    return f"""<html><body style="font-family:Helvetica,Arial,sans-serif;color:#222">
    <h2>Houndstooth digest &mdash; {stamp}</h2>
    <p>{len(top_rows)} jobs scoring &ge;{min_score}. Tailored docs (score &ge; tailoring threshold) link to Google Drive.</p>
    <table style="border-collapse:collapse;font-size:14px" border="1" bordercolor="#ddd">
      <tr style="background:#f2f2f2">
        <th style="padding:6px 10px">Score</th><th style="padding:6px 10px">Title</th>
        <th style="padding:6px 10px">Company</th><th style="padding:6px 10px">Location</th>
        <th style="padding:6px 10px">Docs</th>
      </tr>
      {''.join(rows_html)}
    </table>
    </body></html>"""


def send_email(html: str, cfg: dict) -> None:
    password = os.environ.get("GMAIL_APP_PASSWORD")
    if not password:
        raise RuntimeError("set GMAIL_APP_PASSWORD env var (Google app password)")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Houndstooth digest — {date.today().isoformat()}"
    msg["From"] = cfg["email_from"]
    msg["To"] = cfg["email_to"]
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as smtp:
        smtp.login(cfg["email_from"], password)
        smtp.sendmail(cfg["email_from"], [cfg["email_to"]], msg.as_string())


# ---------------- Orchestration ----------------

def run(config_path="config.yaml"):
    cfg = yaml.safe_load(open(config_path)).get("delivery", {})
    folder_id = cfg.get("gdrive_folder_id") or os.environ.get("GDRIVE_FOLDER_ID", "")
    if not folder_id:
        raise RuntimeError("set delivery.gdrive_folder_id in config.yaml or GDRIVE_FOLDER_ID env var")
    min_score = cfg.get("min_score", 70)

    con = db.connect()

    # 1. Upload docs for tailored-but-undelivered jobs
    pending = con.execute(
        "SELECT * FROM jobs WHERE tailored_at IS NOT NULL AND delivered_at IS NULL "
        "AND verification_status='pass'").fetchall()
    tailored_links = {}
    if pending:
        service = _drive_service()
        for row in pending:
            try:
                links = upload_job_docs(service, folder_id, row)
            except Exception as e:
                print(f"  !! upload failed for {row['id']}: {e}")
                continue
            tailored_links[row["id"]] = links
            con.execute("UPDATE jobs SET delivered_at=?, drive_links=? WHERE id=?",
                        (datetime.now().isoformat(), json.dumps(links), row["id"]))
            con.commit()
            print(f"  uploaded  {row['company'][:28]:<28} {row['title'][:48]}")
    print(f"[deliver] {len(tailored_links)}/{len(pending)} doc pairs uploaded")

    # 2. Digest email covering all current top fits (links included where they exist)
    top_rows = con.execute(
        "SELECT * FROM jobs WHERE score >= ? ORDER BY score DESC, date_posted DESC", (min_score,)
    ).fetchall()
    if not top_rows:
        print("[deliver] no top fits to email; skipping digest")
        return
    send_email(_digest_html(top_rows, tailored_links, min_score), cfg)
    print(f"[deliver] digest emailed to {cfg['email_to']} ({len(top_rows)} jobs)")


if __name__ == "__main__":
    run()
