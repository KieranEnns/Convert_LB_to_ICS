#!/usr/bin/env python3
"""Local web app for converting calendar PDF printouts into .ics files."""

from __future__ import annotations

import argparse
import cgi
import html
import re
import secrets
import shutil
import tempfile
import time
import urllib.parse
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Union

from main import CalendarEvent, events_to_ics, extract_pdf_text, find_calendar_date_range, parse_events

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python 3.8 fallback.
    ZoneInfo = None


HOST = "127.0.0.1"
PORT = 8000
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_SESSIONS = 20
SESSION_TTL_SECONDS = 30 * 60


@dataclass
class ReviewSession:
    token: str
    created_at: float
    events: List[CalendarEvent]
    source_filename: str
    download_filename: str
    calendar_name: str
    timezone_id: Optional[str]
    date_range_label: str


SESSIONS: Dict[str, ReviewSession] = {}


PAGE_CSS = """
:root {
  color-scheme: light;
  --bg: #f5f7f8;
  --panel: #ffffff;
  --text: #172026;
  --muted: #62717b;
  --line: #d9e0e4;
  --accent: #0f766e;
  --accent-strong: #0b5f59;
  --danger: #a83232;
  --warning: #8a5a00;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font: 15px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
main {
  width: min(1180px, calc(100% - 32px));
  margin: 32px auto;
}
.shell {
  display: grid;
  grid-template-columns: minmax(280px, 350px) minmax(0, 1fr);
  gap: 18px;
  align-items: start;
}
section {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
}
.upload-panel { padding: 18px; }
.result-panel { overflow: hidden; }
h1 {
  font-size: 26px;
  line-height: 1.12;
  margin: 0 0 18px;
  letter-spacing: 0;
}
h2 {
  font-size: 17px;
  line-height: 1.25;
  margin: 0;
  letter-spacing: 0;
}
p {
  color: var(--muted);
  margin: 8px 0 0;
}
label {
  display: block;
  color: var(--muted);
  font-size: 13px;
  font-weight: 650;
  margin: 14px 0 6px;
}
input[type="file"],
input[type="number"],
input[type="text"],
input[type="date"],
input[type="time"],
select {
  width: 100%;
  min-height: 38px;
  color: var(--text);
  background: #fff;
  border: 1px solid #c9d3d8;
  border-radius: 6px;
  padding: 7px 9px;
  font: inherit;
}
input[type="file"] { padding: 8px; }
input[type="checkbox"] {
  width: 16px;
  height: 16px;
}
td input[type="text"],
td input[type="date"],
td input[type="time"] {
  min-width: 0;
}
td input[type="text"] {
  min-width: 160px;
}
td input[type="date"] {
  min-width: 138px;
}
td input[type="time"] {
  min-width: 96px;
}
.row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
.check {
  display: flex;
  gap: 9px;
  align-items: center;
  margin-top: 12px;
  color: var(--text);
  font-weight: 500;
}
button,
.button {
  appearance: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 42px;
  border: 1px solid transparent;
  border-radius: 6px;
  padding: 9px 14px;
  background: var(--accent);
  color: #fff;
  font: inherit;
  font-weight: 700;
  text-decoration: none;
  cursor: pointer;
}
button:hover,
.button:hover { background: var(--accent-strong); }
.secondary {
  background: #fff;
  color: var(--text);
  border-color: #c9d3d8;
}
.secondary:hover { background: #eef3f4; }
.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 18px;
}
.icon {
  width: 18px;
  height: 18px;
  display: inline-block;
}
.drop {
  border: 1px dashed #b7c4ca;
  border-radius: 8px;
  padding: 14px;
  background: #fbfcfc;
}
.status {
  padding: 14px 16px;
  border-bottom: 1px solid var(--line);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.status small {
  color: var(--muted);
  display: block;
  margin-top: 2px;
}
.error,
.warning {
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 14px;
}
.error {
  color: var(--danger);
  background: #fff7f6;
  border: 1px solid #e7b7b0;
}
.warning {
  color: var(--warning);
  background: #fff9ec;
  border: 1px solid #e9cd92;
}
.empty {
  min-height: 330px;
  display: grid;
  place-items: center;
  color: var(--muted);
  text-align: center;
  padding: 24px;
}
.file-symbol {
  width: 58px;
  height: 72px;
  margin: 0 auto 14px;
  border: 2px solid #9db0b8;
  border-radius: 7px;
  position: relative;
  background: linear-gradient(#fff, #f0f5f6);
}
.file-symbol::before {
  content: "";
  position: absolute;
  right: -2px;
  top: -2px;
  border-top: 18px solid var(--bg);
  border-left: 18px solid #b7c4ca;
  width: 0;
  height: 0;
}
.file-symbol::after {
  content: "";
  position: absolute;
  left: 12px;
  right: 12px;
  top: 34px;
  height: 18px;
  border-top: 3px solid var(--accent);
  border-bottom: 3px solid var(--accent);
}
table {
  width: 100%;
  min-width: 980px;
  border-collapse: collapse;
}
th,
td {
  text-align: left;
  vertical-align: middle;
  border-bottom: 1px solid var(--line);
  padding: 9px 10px;
}
th {
  color: var(--muted);
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0;
  background: #f8fafb;
  position: sticky;
  top: 0;
  z-index: 1;
}
th:first-child,
td:first-child {
  width: 54px;
  text-align: center;
}
.table-wrap {
  max-height: 590px;
  overflow: auto;
}
.meta {
  color: var(--muted);
  font-size: 12px;
}
.hint {
  color: var(--muted);
  font-size: 12px;
  margin: 5px 0 0;
}
.check-wrap {
  margin-top: 12px;
}
.check-wrap .check {
  margin-top: 0;
}
.check-wrap .hint {
  margin-left: 25px;
}
@media (max-width: 860px) {
  main { width: min(100% - 20px, 720px); margin: 18px auto; }
  .shell { grid-template-columns: 1fr; }
  .row { grid-template-columns: 1fr; }
  .status { align-items: flex-start; flex-direction: column; }
}
"""


def svg_icon(kind: str) -> str:
    if kind == "upload":
        return (
            '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true" fill="none" '
            'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
            '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>'
            '<path d="M17 8l-5-5-5 5"/><path d="M12 3v12"/></svg>'
        )
    if kind == "download":
        return (
            '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true" fill="none" '
            'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
            '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>'
            '<path d="M7 10l5 5 5-5"/><path d="M12 15V3"/></svg>'
        )
    return ""


def html_escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def safe_download_filename(filename: str) -> str:
    stem = Path(filename or "calendar").stem
    stem = re.sub(r"[^A-Za-z0-9._ -]+", "", stem).strip(" ._-")
    if not stem:
        stem = "calendar"
    return f"{stem}.ics"


def parse_int(value: object, fallback: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return fallback
    return min(max(parsed, minimum), maximum)


def first_field(value: Union[cgi.FieldStorage, List[cgi.FieldStorage], None]) -> Optional[cgi.FieldStorage]:
    if isinstance(value, list):
        return value[0] if value else None
    return value


def validate_timezone(value: str) -> str:
    timezone_id = value.strip() or "America/Winnipeg"
    if timezone_id.startswith("/") or ".." in timezone_id or not re.fullmatch(r"[A-Za-z0-9_./+-]+", timezone_id):
        raise ValueError("Timezone must be an IANA name such as America/Winnipeg.")
    if ZoneInfo is not None:
        try:
            ZoneInfo(timezone_id)
        except Exception as exc:
            raise ValueError(f"Unknown timezone: {timezone_id}") from exc
    return timezone_id


def has_pdf_signature(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(1024).lstrip().startswith(b"%PDF-")
    except OSError:
        return False


def calendar_date_range_label(text: str) -> str:
    calendar_range = find_calendar_date_range(text)
    if not calendar_range:
        raise ValueError("No calendar date range was found in the PDF.")
    start, end = calendar_range
    return f"{start.isoformat()} to {end.isoformat()}"


def infer_default_year_from_calendar_text(text: str) -> int:
    calendar_range = find_calendar_date_range(text)
    if not calendar_range:
        raise ValueError("No calendar date range was found in the PDF.")
    return calendar_range[0].year


def make_token() -> str:
    return secrets.token_urlsafe(18)


def purge_sessions() -> None:
    now = time.time()
    expired = [token for token, session in SESSIONS.items() if now - session.created_at > SESSION_TTL_SECONDS]
    for token in expired:
        del SESSIONS[token]

    while len(SESSIONS) > MAX_SESSIONS:
        oldest = min(SESSIONS, key=lambda token: SESSIONS[token].created_at)
        del SESSIONS[oldest]


def store_session(
    events: List[CalendarEvent],
    source_filename: str,
    calendar_name: str,
    timezone_id: Optional[str],
    date_range_label: str,
) -> ReviewSession:
    purge_sessions()
    token = make_token()
    session = ReviewSession(
        token=token,
        created_at=time.time(),
        events=events,
        source_filename=source_filename,
        download_filename=safe_download_filename(source_filename),
        calendar_name=calendar_name,
        timezone_id=timezone_id,
        date_range_label=date_range_label,
    )
    SESSIONS[token] = session
    purge_sessions()
    return session


def session_for_token(token: str) -> Optional[ReviewSession]:
    purge_sessions()
    return SESSIONS.get(token)


def event_date_value(event: CalendarEvent) -> str:
    return event.start_date.isoformat()


def event_end_date_value(event: CalendarEvent) -> str:
    return event.effective_end_date.isoformat()


def event_time_value(event: CalendarEvent, kind: str) -> str:
    if event.all_day:
        return ""
    value = event.start_time if kind == "start" else event.end_time
    return value.strftime("%H:%M") if value else ""


def event_review_rows(events: Iterable[CalendarEvent]) -> str:
    rows: List[str] = []
    for index, event in enumerate(events):
        all_day_checked = "checked" if event.all_day else ""
        rows.append(
            "<tr>"
            f'<td><input type="checkbox" name="include_{index}" checked aria-label="Include event {index + 1}"></td>'
            f'<td><input name="title_{index}" type="text" value="{html_escape(event.title)}" required></td>'
            f'<td><input name="start_date_{index}" type="date" value="{event_date_value(event)}" required></td>'
            f'<td><input name="start_time_{index}" type="time" value="{event_time_value(event, "start")}"></td>'
            f'<td><input name="end_date_{index}" type="date" value="{event_end_date_value(event)}" required></td>'
            f'<td><input name="end_time_{index}" type="time" value="{event_time_value(event, "end")}"></td>'
            f'<td><input name="location_{index}" type="text" value="{html_escape(event.location)}"></td>'
            f'<td><input type="checkbox" name="all_day_{index}" {all_day_checked} aria-label="All day event {index + 1}"></td>'
            "</tr>"
        )
    return "\n".join(rows)


def parse_iso_date(value: str, fallback: date) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return fallback


def parse_time_value(value: str) -> Optional[datetime.time]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError:
        return None


def selected_events_from_form(form: cgi.FieldStorage, session: ReviewSession) -> List[CalendarEvent]:
    selected: List[CalendarEvent] = []
    for index, original in enumerate(session.events):
        if not form.getfirst(f"include_{index}"):
            continue

        title = (form.getfirst(f"title_{index}", original.title) or "").strip()
        if not title:
            continue

        start_date = parse_iso_date(form.getfirst(f"start_date_{index}", ""), original.start_date)
        end_date = parse_iso_date(form.getfirst(f"end_date_{index}", ""), original.effective_end_date)
        all_day = bool(form.getfirst(f"all_day_{index}"))
        location = (form.getfirst(f"location_{index}", original.location) or "").strip()

        if all_day:
            selected.append(
                CalendarEvent(
                    title=title,
                    start_date=start_date,
                    end_date=end_date if end_date > start_date else start_date + timedelta(days=1),
                    all_day=True,
                    location=location,
                    description=original.description,
                    source_line=original.source_line,
                )
            )
            continue

        start_time = parse_time_value(form.getfirst(f"start_time_{index}", ""))
        end_time = parse_time_value(form.getfirst(f"end_time_{index}", ""))
        if not start_time or not end_time:
            continue
        if end_date < start_date:
            end_date = start_date

        selected.append(
            CalendarEvent(
                title=title,
                start_date=start_date,
                start_time=start_time,
                end_date=end_date,
                end_time=end_time,
                all_day=False,
                location=location,
                description=original.description,
                source_line=original.source_line,
            )
        )

    return selected


def form_from_request(handler: BaseHTTPRequestHandler, content_length: int) -> cgi.FieldStorage:
    return cgi.FieldStorage(
        fp=handler.rfile,
        headers=handler.headers,
        environ={
            "REQUEST_METHOD": "POST",
            "CONTENT_TYPE": handler.headers.get("Content-Type", ""),
            "CONTENT_LENGTH": str(content_length),
        },
    )


def render_page(
    *,
    error: str = "",
    warning: str = "",
    session: Optional[ReviewSession] = None,
    form_values: Optional[Dict[str, str]] = None,
) -> bytes:
    values = {
        "timezone": "America/Winnipeg",
        "default_duration": "60",
        "calendar_name": "",
        "floating_times": "",
        "timed_only": "",
    }
    if form_values:
        values.update(form_values)

    result_html = ""
    if session:
        rows = event_review_rows(session.events)
        expires_at = datetime.fromtimestamp(session.created_at + SESSION_TTL_SECONDS).strftime("%H:%M")
        result_html = f"""
        <section class="result-panel">
          <form action="/download" method="post">
            <input type="hidden" name="token" value="{html_escape(session.token)}">
            <div class="status">
              <div>
                <h2>{html_escape(len(session.events))} events ready for review</h2>
                <small>{html_escape(session.source_filename)}. Date range {html_escape(session.date_range_label)}. Review rows, uncheck mistakes, then download. Expires around {expires_at}.</small>
              </div>
              <button type="submit">{svg_icon("download")}Download ICS</button>
            </div>
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Use</th><th>Title</th><th>Start date</th><th>Start</th>
                    <th>End date</th><th>End</th><th>Location</th><th>All day</th>
                  </tr>
                </thead>
                <tbody>{rows}</tbody>
              </table>
            </div>
          </form>
        </section>
        """
    else:
        result_html = """
        <section class="result-panel empty">
          <div>
            <div class="file-symbol" aria-hidden="true"></div>
            <h2>No calendar loaded</h2>
            <p>Upload a text-based calendar PDF to preview the detected events.</p>
          </div>
        </section>
        """

    error_html = f'<div class="error">{html_escape(error)}</div>' if error else ""
    warning_html = f'<div class="warning">{html_escape(warning)}</div>' if warning else ""
    floating_checked = "checked" if values.get("floating_times") == "on" else ""
    timed_only_checked = "checked" if values.get("timed_only") == "on" else ""

    page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Calendar PDF to ICS</title>
  <style>{PAGE_CSS}</style>
</head>
<body>
<main>
  <h1>Calendar PDF to ICS</h1>
  <div class="shell">
    <section class="upload-panel">
      {error_html}
      {warning_html}
      <form action="/convert" method="post" enctype="multipart/form-data">
        <div class="drop">
          <label for="pdf">PDF file</label>
          <input id="pdf" name="pdf" type="file" accept="application/pdf,.pdf" required>
        </div>

        <label for="calendar_name">Calendar name</label>
        <input id="calendar_name" name="calendar_name" type="text" value="{html_escape(values["calendar_name"])}" placeholder="Imported Calendar">
        <p class="hint">Used as the calendar title in the downloaded ICS. Leave blank to use the PDF filename.</p>

        <p class="meta">Numeric dates in uploaded PDFs are read as MM/DD. The year is inferred from the printed date range.</p>

        <div class="row">
          <div>
            <label for="timezone">Timezone</label>
            <input id="timezone" name="timezone" type="text" value="{html_escape(values["timezone"])}">
            <p class="hint">Use the timezone the schedule times are written in. For Manitoba, keep America/Winnipeg.</p>
          </div>
          <div>
            <label for="default_duration">Default minutes</label>
            <input id="default_duration" name="default_duration" type="number" min="1" max="1440" value="{html_escape(values["default_duration"])}">
            <p class="hint">Only used if the PDF has a start time but no end time.</p>
          </div>
        </div>

        <div class="check-wrap">
          <label class="check" for="floating_times">
            <input id="floating_times" name="floating_times" type="checkbox" {floating_checked}>
            Floating times
          </label>
          <p class="hint">Leaves timezone metadata out of the ICS. Usually keep this unchecked.</p>
        </div>
        <div class="check-wrap">
          <label class="check" for="timed_only">
            <input id="timed_only" name="timed_only" type="checkbox" {timed_only_checked}>
            Timed events only
          </label>
          <p class="hint">Skips all-day notes or entries without a recognizable time.</p>
        </div>

        <div class="actions">
          <button type="submit">{svg_icon("upload")}Convert PDF</button>
        </div>
      </form>
      <p class="meta">Uploaded PDFs are deleted after parsing; review sessions expire after 30 minutes.</p>
    </section>
    {result_html}
  </div>
</main>
</body>
</html>"""
    return page.encode("utf-8")


class CalendarConverterHandler(BaseHTTPRequestHandler):
    server_version = "CalendarPdfWeb/2.0"

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self.send_html(render_page())
            return
        if parsed.path == "/download":
            self.handle_download_all(parsed.query)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Page not found")

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/convert":
            self.handle_convert()
            return
        if parsed.path == "/download":
            self.handle_download_selected()
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Page not found")

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.address_string()} - {format % args}")

    def send_html(self, body: bytes, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_ics(self, body: str, filename: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/calendar; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def content_length(self) -> int:
        return parse_int(self.headers.get("Content-Length", "0"), 0, 0, MAX_UPLOAD_BYTES + 1)

    def handle_download_all(self, query: str) -> None:
        params = urllib.parse.parse_qs(query)
        token = params.get("token", [""])[0]
        session = session_for_token(token)
        if not session:
            self.send_error(HTTPStatus.NOT_FOUND, "Review session expired")
            return
        ics = events_to_ics(session.events, session.calendar_name, session.timezone_id, Path(session.source_filename))
        self.send_ics(ics, session.download_filename)

    def handle_download_selected(self) -> None:
        content_length = self.content_length()
        form = form_from_request(self, content_length)
        token = form.getfirst("token", "")
        session = session_for_token(token)
        if not session:
            self.send_html(render_page(error="That review session has expired. Upload the PDF again."), HTTPStatus.GONE)
            return

        events = selected_events_from_form(form, session)
        if not events:
            self.send_html(
                render_page(
                    error="No selected rows had enough event information to export.",
                    session=session,
                ),
                HTTPStatus.UNPROCESSABLE_ENTITY,
            )
            return

        ics = events_to_ics(events, session.calendar_name, session.timezone_id, Path(session.source_filename))
        self.send_ics(ics, session.download_filename)

    def handle_convert(self) -> None:
        content_length = self.content_length()
        if content_length > MAX_UPLOAD_BYTES:
            self.send_html(render_page(error="The uploaded PDF is larger than 25 MB."), HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return

        form = form_from_request(self, content_length)
        form_values = {
            "timezone": form.getfirst("timezone", "America/Winnipeg").strip() or "America/Winnipeg",
            "default_duration": form.getfirst("default_duration", "60"),
            "calendar_name": form.getfirst("calendar_name", "").strip(),
            "floating_times": "on" if form.getfirst("floating_times") else "",
            "timed_only": "on" if form.getfirst("timed_only") else "",
        }

        upload = first_field(form["pdf"] if "pdf" in form else None)
        if upload is None or not getattr(upload, "filename", ""):
            self.send_html(render_page(error="Choose a PDF file.", form_values=form_values), HTTPStatus.BAD_REQUEST)
            return

        original_filename = Path(upload.filename).name
        if not original_filename.lower().endswith(".pdf"):
            self.send_html(render_page(error="The uploaded file must be a PDF.", form_values=form_values), HTTPStatus.BAD_REQUEST)
            return

        default_duration = parse_int(form_values["default_duration"], 60, 1, 1440)

        try:
            timezone_id = None if form_values["floating_times"] == "on" else validate_timezone(form_values["timezone"])
        except ValueError as exc:
            self.send_html(render_page(error=str(exc), form_values=form_values), HTTPStatus.BAD_REQUEST)
            return

        calendar_name = form_values["calendar_name"] or Path(original_filename).stem.replace("_", " ").replace("-", " ")
        temp_path = ""
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_file:
                temp_path = temp_file.name
                shutil.copyfileobj(upload.file, temp_file)

            pdf_path = Path(temp_path)
            if not has_pdf_signature(pdf_path):
                self.send_html(render_page(error="That file does not appear to be a valid PDF.", form_values=form_values), HTTPStatus.BAD_REQUEST)
                return

            text = extract_pdf_text(pdf_path)
            try:
                default_year = infer_default_year_from_calendar_text(text)
                date_range_label = calendar_date_range_label(text)
            except ValueError as exc:
                self.send_html(
                    render_page(
                        error=f"{exc} This converter expects PDFs with a printed MM/DD/YYYY-MM/DD/YYYY range.",
                        form_values=form_values,
                    ),
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                )
                return

            events = parse_events(
                text,
                default_year=default_year,
                date_order="mdy",
                default_duration_minutes=default_duration,
                timed_only=form_values["timed_only"] == "on",
            )
            if not events:
                self.send_html(
                    render_page(
                        error="No events were detected. If this is a scan or photo PDF, run OCR first.",
                        form_values=form_values,
                    ),
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                )
                return

            warning = ""
            if len(events) < 3:
                warning = "Only a few events were detected. Check the review table carefully before downloading."

            session = store_session(events, original_filename, calendar_name, timezone_id, date_range_label)
            self.send_html(render_page(session=session, form_values=form_values, warning=warning))
        except Exception as exc:
            self.send_html(render_page(error=str(exc), form_values=form_values), HTTPStatus.INTERNAL_SERVER_ERROR)
        finally:
            if temp_path:
                try:
                    Path(temp_path).unlink()
                except FileNotFoundError:
                    pass


def run(host: str = HOST, port: int = PORT) -> None:
    server = ThreadingHTTPServer((host, port), CalendarConverterHandler)
    print(f"Calendar PDF web app running at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the local Calendar PDF to ICS web app.")
    parser.add_argument("--host", default=HOST, help="Host interface to bind. Defaults to 127.0.0.1.")
    parser.add_argument("--port", type=int, default=PORT, help="Port to bind. Defaults to 8000.")
    args = parser.parse_args()
    run(args.host, args.port)
