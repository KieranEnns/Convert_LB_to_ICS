# Calendar PDF to ICS Converter

This program reads a text-based calendar PDF printout and creates an `.ics` file
that can be imported into Google Calendar, Apple Calendar, Outlook, and most
other calendar apps.

It supports both simple list-style printouts and week-grid printouts where
events are positioned under Sunday-Saturday columns.

## Web App

## Download The Ready-To-Use Mac App

The easiest option for non-technical users is the packaged macOS app from
GitHub Releases:

1. Open the repository's **Releases** page.
2. Download `LB-to-ICS-Converter-macOS.zip`.
3. Unzip it.
4. Double-click `LB to ICS Converter.app`.
5. If macOS blocks it because it is unsigned, right-click the app, choose
   **Open**, then confirm.

The app opens your browser to the local converter. No Terminal commands or
Python package installation are needed for this packaged version.

## Run From Source

Start the local web app:

```bash
python3 web_app.py
```

Open this address in your browser:

```text
http://127.0.0.1:8000
```

Upload a PDF, confirm the options, click **Convert PDF**, review the detected
events, uncheck or edit rows as needed, then download the generated `.ics` file.
Numeric dates in uploaded PDFs are treated as `MM/DD`, and the year is inferred
from the printed calendar date range.

If port `8000` is already in use:

```bash
python3 web_app.py --port 8001
```

On macOS, you can also double-click `Start Calendar Converter.command` from the
project folder. If you want it on your Desktop, make an alias to it instead of
moving the file, because the launcher expects to live beside `web_app.py`.

## Command Line Usage

```bash
python3 main.py path/to/calendar.pdf path/to/calendar.ics --year 2026 --preview
```

If you omit the output path, the program writes beside the PDF with an `.ics`
extension:

```bash
python3 main.py path/to/calendar.pdf --year 2026 --preview
```

Useful options:

```bash
python3 main.py calendar.pdf --year 2026 --timezone America/Winnipeg
python3 main.py calendar.pdf --year 2026 --floating-times
python3 main.py calendar.pdf --year 2026 --timed-only
python3 main.py calendar.pdf --year 2026 --dump-text extracted.txt
```

For the command-line tool, use `--year` when the calendar printout lists dates
like `Sep 15` without a year. Numeric dates default to `MM/DD`. Use
`--dump-text` if the generated events look wrong; PDF text extraction often
reveals formatting quirks that can be handled by adjusting the parser.

## PDF Requirements

The PDF must contain selectable text. If the printout is a scan or photo, run
OCR first, then use the OCR PDF with this converter.

The program currently recognizes common lines such as:

```text
Monday, September 14, 2026
9:00 AM - 10:30 AM Staff Meeting @ Room 204
Sep 15 2pm Parent Meeting
09/16/2026 School Closed
Location: Main Office
Notes: Bring reports
```

For grid-style PDFs, it uses preserved PDF layout spacing to connect each event
cell to the date at the top of that column.

The app also fills default locations for known schedule labels:

- `Hospitalist`, including on-call or post-hospitalist entries: Boundary Trails
  Health Centre, Hwy 3 & Hwy 14, Winkler, MB R6W 1H8, Canada
- `MUCAM`: Menzies Medical Centre, 130-30 Stephen Street, Morden, MB R6M 2G3,
  Canada

## Install Dependency

The PDF reader dependency is:

```bash
python3 -m pip install pypdf
```

Or install from the included requirements file:

```bash
python3 -m pip install -r requirements.txt
```

## Download From GitHub

For users who want to run from source instead of the packaged app:

1. Open the GitHub repository page.
2. Click **Code**.
3. Click **Download ZIP**.
4. Unzip the folder.
5. Open Terminal in that folder.
6. Run `python3 -m pip install -r requirements.txt`.
7. Run `python3 web_app.py` or double-click `Start Calendar Converter.command`
   on macOS.

For users using Git:

```bash
git clone REPOSITORY_URL
cd REPOSITORY_FOLDER
python3 -m pip install -r requirements.txt
python3 web_app.py
```

## Build A macOS Release

Maintainers can build the no-Terminal macOS app with:

```bash
./scripts/build_macos_app.sh
```

This creates:

```text
LB-to-ICS-Converter-macOS.zip
```

Upload that ZIP file to a GitHub Release so users can download and run the app
without installing Python packages manually.

## Local Web App Safeguards

- Uploaded files are written to a temporary file and deleted after parsing.
- Review sessions are stored in memory and expire after 30 minutes.
- Uploads are limited to 25 MB.
- The app rejects files that do not look like PDFs.
- Timezones are validated as IANA names, such as `America/Winnipeg`.
- Users can edit or exclude detected events before downloading the `.ics`.
- The web app infers the schedule year from the printed `MM/DD/YYYY-MM/DD/YYYY`
  calendar range.

## Before Public Deployment

This app is designed for local use. A public or shared deployment should add
HTTPS, authentication if schedules are private, CSRF protection, durable job
storage, rate limiting, structured logging without sensitive PDF contents,
malware scanning if required by policy, and a production WSGI/ASGI server such
as Gunicorn/Uvicorn behind a reverse proxy.
