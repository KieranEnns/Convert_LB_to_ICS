#!/usr/bin/env python3
"""Deployable Flask website for converting calendar PDFs into ICS files."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Optional

from flask import Flask, Response, abort, make_response, request

from main import events_to_ics, extract_pdf_text, parse_events
from web_app import (
    MAX_UPLOAD_BYTES,
    calendar_date_range_label,
    has_pdf_signature,
    infer_default_year_from_calendar_text,
    parse_int,
    render_page,
    session_for_token,
    selected_events_from_form,
    store_session,
    validate_timezone,
)


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES


class FlaskFormAdapter:
    """Small adapter so existing review-form parsing works with Flask forms."""

    def __init__(self, values):
        self.values = values

    def getfirst(self, key: str, default: Optional[str] = None) -> Optional[str]:
        return self.values.get(key, default)


def html_response(body: bytes, status: int = 200) -> Response:
    response = make_response(body, status)
    response.headers["Content-Type"] = "text/html; charset=utf-8"
    return response


def ics_response(body: str, filename: str) -> Response:
    response = make_response(body, 200)
    response.headers["Content-Type"] = "text/calendar; charset=utf-8"
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@app.after_request
def add_security_headers(response: Response) -> Response:
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    return response


@app.get("/")
def index() -> Response:
    return html_response(render_page())


@app.get("/healthz")
def healthz() -> Response:
    return Response("ok\n", mimetype="text/plain")


@app.post("/convert")
def convert() -> Response:
    form_values = {
        "timezone": request.form.get("timezone", "America/Winnipeg").strip() or "America/Winnipeg",
        "default_duration": request.form.get("default_duration", "60"),
        "calendar_name": request.form.get("calendar_name", "").strip(),
        "floating_times": "on" if request.form.get("floating_times") else "",
        "timed_only": "on" if request.form.get("timed_only") else "",
    }

    upload = request.files.get("pdf")
    if upload is None or not upload.filename:
        return html_response(render_page(error="Choose a PDF file.", form_values=form_values), 400)

    original_filename = Path(upload.filename).name
    if not original_filename.lower().endswith(".pdf"):
        return html_response(render_page(error="The uploaded file must be a PDF.", form_values=form_values), 400)

    default_duration = parse_int(form_values["default_duration"], 60, 1, 1440)
    try:
        timezone_id = None if form_values["floating_times"] == "on" else validate_timezone(form_values["timezone"])
    except ValueError as exc:
        return html_response(render_page(error=str(exc), form_values=form_values), 400)

    calendar_name = form_values["calendar_name"] or Path(original_filename).stem.replace("_", " ").replace("-", " ")
    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_file:
            temp_path = temp_file.name
            upload.save(temp_file)

        pdf_path = Path(temp_path)
        if not has_pdf_signature(pdf_path):
            return html_response(render_page(error="That file does not appear to be a valid PDF.", form_values=form_values), 400)

        text = extract_pdf_text(pdf_path)
        try:
            default_year = infer_default_year_from_calendar_text(text)
            date_range_label = calendar_date_range_label(text)
        except ValueError as exc:
            return html_response(
                render_page(
                    error=f"{exc} This converter expects PDFs with a printed MM/DD/YYYY-MM/DD/YYYY range.",
                    form_values=form_values,
                ),
                422,
            )

        events = parse_events(
            text,
            default_year=default_year,
            date_order="mdy",
            default_duration_minutes=default_duration,
            timed_only=form_values["timed_only"] == "on",
        )
        if not events:
            return html_response(
                render_page(
                    error="No events were detected. If this is a scan or photo PDF, run OCR first.",
                    form_values=form_values,
                ),
                422,
            )

        warning = ""
        if len(events) < 3:
            warning = "Only a few events were detected. Check the review table carefully before downloading."

        session = store_session(events, original_filename, calendar_name, timezone_id, date_range_label)
        return html_response(render_page(session=session, form_values=form_values, warning=warning))
    except Exception as exc:
        return html_response(render_page(error=str(exc), form_values=form_values), 500)
    finally:
        if temp_path:
            try:
                Path(temp_path).unlink()
            except FileNotFoundError:
                pass


@app.get("/download")
def download_all() -> Response:
    token = request.args.get("token", "")
    session = session_for_token(token)
    if not session:
        abort(404, "Review session expired")
    ics = events_to_ics(session.events, session.calendar_name, session.timezone_id, Path(session.source_filename))
    return ics_response(ics, session.download_filename)


@app.post("/download")
def download_selected() -> Response:
    token = request.form.get("token", "")
    session = session_for_token(token)
    if not session:
        return html_response(render_page(error="That review session has expired. Upload the PDF again."), 410)

    events = selected_events_from_form(FlaskFormAdapter(request.form), session)
    if not events:
        return html_response(
            render_page(error="No selected rows had enough event information to export.", session=session),
            422,
        )

    ics = events_to_ics(events, session.calendar_name, session.timezone_id, Path(session.source_filename))
    return ics_response(ics, session.download_filename)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=False)
