#!/usr/bin/env python3
"""Convert a text-based calendar PDF printout into an importable .ics file.

The parser is deliberately conservative: it looks for date headings, date/time
lines, and common event labels, then writes standard iCalendar VEVENT entries.
If the PDF is a scan/photo with no selectable text, OCR it first.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple


MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

WEEKDAY_RE = r"(?:mon(?:day)?|tue(?:sday)?|wed(?:nesday)?|thu(?:rsday)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)"
MONTH_RE = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:t(?:ember)?|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)

DATE_PATTERNS = [
    re.compile(
        rf"(?P<prefix>\b{WEEKDAY_RE},?\s+)?"
        rf"(?P<month>{MONTH_RE})\.?\s+"
        r"(?P<day>\d{1,2})(?!\d)(?:st|nd|rd|th)?"
        r"(?:,?\s+(?P<year>\d{2,4}))?",
        re.IGNORECASE,
    ),
    re.compile(
        rf"(?P<prefix>\b{WEEKDAY_RE},?\s+)?"
        r"(?P<day>\d{1,2})(?!\d)(?:st|nd|rd|th)?\s+"
        rf"(?P<month>{MONTH_RE})\.?"
        r"(?:,?\s+(?P<year>\d{2,4}))?",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?P<year>\d{4})[-/.](?P<month>\d{1,2})[-/.](?P<day>\d{1,2})\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?P<first>\d{1,2})[-/.](?P<second>\d{1,2})(?:[-/.](?P<year>\d{2,4}))?\b",
        re.IGNORECASE,
    ),
]

TIME_TOKEN_RE = r"\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?"
TIME_RANGE_RE = re.compile(
    rf"(?P<start>{TIME_TOKEN_RE})\s*(?:-|to|until|through|thru|–|—)\s*(?P<end>{TIME_TOKEN_RE})",
    re.IGNORECASE,
)
SINGLE_TIME_RE = re.compile(
    r"(?P<start>\b\d{1,2}(?::\d{2}\s*)?(?:a\.?m\.?|p\.?m\.?)\b|\b\d{1,2}:\d{2}\b)",
    re.IGNORECASE,
)

LOCATION_LABEL_RE = re.compile(r"^(?:location|where|place|room)\s*:\s*(?P<value>.+)$", re.IGNORECASE)
DESCRIPTION_LABEL_RE = re.compile(r"^(?:description|details|notes?|note)\s*:\s*(?P<value>.+)$", re.IGNORECASE)
INLINE_LOCATION_RE = re.compile(r"\s+(?:@|location:|where:)\s*(?P<location>[^;|]+)\s*$", re.IGNORECASE)
TITLE_LABEL_RE = re.compile(r"^(?:event|title|summary|appointment)\s*:\s*", re.IGNORECASE)
DATE_RANGE_RE = re.compile(
    r"\b(?P<start_month>\d{1,2})/(?P<start_day>\d{1,2})/(?P<start_year>\d{4})\s*-\s*"
    r"(?P<end_month>\d{1,2})/(?P<end_day>\d{1,2})/(?P<end_year>\d{4})\b"
)
WEEK_GRID_DAY_RE = re.compile(
    r"\b(?P<weekday>Sun|Mon|Tue|W\s*ed|Wed|Thu|Fri|Sat)\s+(?P<month>\d{1,2})/(?P<day>\d{1,2})\b",
    re.IGNORECASE,
)

DEFAULT_LOCATION_RULES = [
    (
        re.compile(r"\bmucam\b", re.IGNORECASE),
        "Menzies Medical Centre, 130-30 Stephen Street, Morden, MB R6M 2G3, Canada",
    ),
    (
        re.compile(r"\bhospitalist\b", re.IGNORECASE),
        "Boundary Trails Health Centre, Hwy 3 & Hwy 14, Winkler, MB R6W 1H8, Canada",
    ),
]


@dataclass
class DateMatch:
    value: date
    start: int
    end: int


@dataclass
class TimeMatch:
    start_time: time
    end_time: time
    end_is_next_day: bool
    start: int
    end: int


@dataclass
class CalendarEvent:
    title: str
    start_date: date
    start_time: Optional[time] = None
    end_date: Optional[date] = None
    end_time: Optional[time] = None
    all_day: bool = False
    location: str = ""
    description: str = ""
    source_line: str = ""

    @property
    def effective_end_date(self) -> date:
        if self.end_date:
            return self.end_date
        if self.all_day:
            return self.start_date + timedelta(days=1)
        return self.start_date


@dataclass
class WeekColumn:
    value: date
    start: int
    end: int


def extract_pdf_text(pdf_path: Path) -> str:
    """Extract selectable text from a PDF using pypdf."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError(
            "The 'pypdf' package is required for PDF reading. Install it with: python3 -m pip install pypdf"
        ) from exc

    reader = PdfReader(str(pdf_path))
    page_text: List[str] = []
    for page_number, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text(extraction_mode="layout") or ""
        except TypeError:
            text = page.extract_text() or ""
        if text.strip():
            page_text.append(f"\n--- Page {page_number} ---\n{text}")
    extracted = "\n".join(page_text).strip()
    if not extracted:
        raise RuntimeError(
            "No selectable text was found in the PDF. If this is a scanned printout, run OCR first "
            "and then try again."
        )
    return extracted


def normalize_text(text: str) -> List[str]:
    replacements = {
        "\u00a0": " ",
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2022": "-",
        "\uf0b7": "-",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)

    lines = []
    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if line:
            lines.append(line)
    return lines


def clean_year(raw_year: Optional[str], default_year: int) -> int:
    if not raw_year:
        return default_year
    year = int(raw_year)
    if year < 100:
        return 2000 + year if year < 70 else 1900 + year
    return year


def parse_month(value: str) -> int:
    key = value.lower().rstrip(".")
    if key not in MONTHS:
        raise ValueError(f"Unknown month: {value}")
    return MONTHS[key]


def build_date(year: int, month: int, day: int) -> Optional[date]:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def find_date(line: str, default_year: int, date_order: str) -> Optional[DateMatch]:
    for pattern in DATE_PATTERNS:
        match = pattern.search(line)
        if not match:
            continue

        groups = match.groupdict()
        if "first" in groups and groups.get("first"):
            first = int(groups["first"])
            second = int(groups["second"])
            year = clean_year(groups.get("year"), default_year)
            if date_order == "dmy":
                day, month = first, second
            elif date_order == "ymd":
                continue
            else:
                month, day = first, second
        else:
            year = clean_year(groups.get("year"), default_year)
            month_value = groups.get("month")
            month = parse_month(month_value) if month_value and not month_value.isdigit() else int(month_value)
            day = int(groups["day"])

        parsed = build_date(year, month, day)
        if parsed:
            return DateMatch(parsed, match.start(), match.end())
    return None


def strip_span(value: str, start: int, end: int) -> str:
    return f"{value[:start]} {value[end:]}".strip()


def is_date_header(line: str, match: DateMatch) -> bool:
    remainder = strip_span(line, match.start, match.end)
    remainder = re.sub(rf"\b{WEEKDAY_RE}\b", "", remainder, flags=re.IGNORECASE)
    remainder = re.sub(r"[-,.:|/()\[\]\s]+", "", remainder)
    return not remainder


def has_time_hint(value: str) -> bool:
    lower = value.lower()
    return bool(
        re.search(r"\d{1,2}:\d{2}", lower)
        or re.search(r"\d{1,2}\s*(?:a\.?m\.?|p\.?m\.?)", lower)
        or "noon" in lower
        or "midnight" in lower
    )


def parse_time_token(token: str, fallback_meridiem: Optional[str] = None) -> Optional[time]:
    raw = token.strip().lower().replace(".", "")
    if raw == "noon":
        return time(12, 0)
    if raw == "midnight":
        return time(0, 0)

    match = re.fullmatch(r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<meridiem>am|pm)?", raw)
    if not match:
        return None

    hour = int(match.group("hour"))
    minute = int(match.group("minute") or 0)
    meridiem = match.group("meridiem") or fallback_meridiem

    if minute > 59:
        return None
    if meridiem:
        if hour < 1 or hour > 12:
            return None
        if meridiem == "am":
            hour = 0 if hour == 12 else hour
        else:
            hour = hour if hour == 12 else hour + 12
    elif hour > 23:
        return None
    return time(hour, minute)


def extract_meridiem(token: str) -> Optional[str]:
    match = re.search(r"(a\.?m\.?|p\.?m\.?)", token, re.IGNORECASE)
    if not match:
        return None
    return match.group(1).lower().replace(".", "")


def with_date(day: date, clock: time) -> datetime:
    return datetime.combine(day, clock)


def find_time(line: str, event_date: date, default_duration_minutes: int) -> Optional[TimeMatch]:
    range_match = TIME_RANGE_RE.search(line)
    if range_match:
        start_token = range_match.group("start")
        end_token = range_match.group("end")
        if not (has_time_hint(start_token) or has_time_hint(end_token)):
            return None

        start_meridiem = extract_meridiem(start_token)
        end_meridiem = extract_meridiem(end_token)
        start_time = parse_time_token(start_token, start_meridiem or end_meridiem)
        end_time = parse_time_token(end_token, end_meridiem or start_meridiem)

        if start_time and end_time and with_date(event_date, end_time) <= with_date(event_date, start_time):
            if end_meridiem and not start_meridiem:
                opposite = "am" if end_meridiem == "pm" else "pm"
                alternate_start = parse_time_token(start_token, opposite)
                if alternate_start and with_date(event_date, alternate_start) < with_date(event_date, end_time):
                    start_time = alternate_start

        if start_time and end_time:
            end_is_next_day = with_date(event_date, end_time) <= with_date(event_date, start_time)
            return TimeMatch(start_time, end_time, end_is_next_day, range_match.start(), range_match.end())

    single_match = SINGLE_TIME_RE.search(line)
    if not single_match:
        return None

    start_token = single_match.group("start")
    start_time = parse_time_token(start_token)
    if not start_time:
        return None
    start_dt = with_date(event_date, start_time)
    end_dt = start_dt + timedelta(minutes=default_duration_minutes)
    return TimeMatch(
        start_dt.time(),
        end_dt.time(),
        end_dt.date() > start_dt.date(),
        single_match.start(),
        single_match.end(),
    )


def looks_like_noise(line: str) -> bool:
    compact = re.sub(r"\s+", "", line).lower()
    if not compact:
        return True
    if compact.startswith(("http://", "https://")):
        return True
    if compact.startswith("---page") and compact.endswith("---"):
        return True
    if compact in {"sunmontuewedthufrisat", "sundaymondaytuesdaywednesdaythursdayfridaysaturday"}:
        return True
    weekday_count = len(re.findall(rf"\b{WEEKDAY_RE}\b", line, flags=re.IGNORECASE))
    numeric_date_count = len(re.findall(r"\b\d{1,2}/\d{1,2}\b", line))
    if weekday_count >= 4 and numeric_date_count >= 4:
        return True
    if re.fullmatch(r"[-_/.,:|]+", compact):
        return True
    return False


def clean_title(value: str) -> str:
    value = TITLE_LABEL_RE.sub("", value)
    value = re.sub(r"^[\-*•\s]+", "", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip(" -:;,\t")


def meaningful_title(value: str) -> bool:
    return bool(re.search(r"[A-Za-z0-9]{2,}", value))


def default_location_for_title(title: str) -> str:
    for pattern, location in DEFAULT_LOCATION_RULES:
        if pattern.search(title):
            return location
    return ""


def find_calendar_date_range(text: str) -> Optional[Tuple[date, date]]:
    match = DATE_RANGE_RE.search(text)
    if not match:
        return None
    start = build_date(
        int(match.group("start_year")),
        int(match.group("start_month")),
        int(match.group("start_day")),
    )
    end = build_date(
        int(match.group("end_year")),
        int(match.group("end_month")),
        int(match.group("end_day")),
    )
    if not start or not end:
        return None
    return (start, end) if start <= end else (end, start)


def resolve_month_day(
    month: int,
    day: int,
    default_year: int,
    date_range: Optional[Tuple[date, date]],
    previous_date: Optional[date],
) -> Optional[date]:
    years = {default_year - 1, default_year, default_year + 1, default_year + 2}
    if date_range:
        start, end = date_range
        years.update(range(start.year - 1, end.year + 2))
    if previous_date:
        years.update({previous_date.year - 1, previous_date.year, previous_date.year + 1})

    candidates = [candidate for year in years if (candidate := build_date(year, month, day))]
    if not candidates:
        return None

    if date_range:
        start, end = date_range
        in_range = [candidate for candidate in candidates if start <= candidate <= end]
        if in_range:
            return min(in_range)
        return min(candidates, key=lambda candidate: min(abs(candidate - start), abs(candidate - end)))

    if previous_date:
        future = [candidate for candidate in candidates if candidate >= previous_date]
        if future:
            return min(future)

    return build_date(default_year, month, day)


def parse_week_grid_header(
    line: str,
    default_year: int,
    date_range: Optional[Tuple[date, date]],
    previous_date: Optional[date],
) -> Optional[List[WeekColumn]]:
    matches = list(WEEK_GRID_DAY_RE.finditer(line))
    if len(matches) < 5:
        return None

    columns: List[WeekColumn] = []
    last_date = previous_date
    for index, match in enumerate(matches):
        month = int(match.group("month"))
        day = int(match.group("day"))
        resolved = resolve_month_day(month, day, default_year, date_range, last_date)
        if not resolved:
            return None
        next_start = matches[index + 1].start() if index + 1 < len(matches) else max(len(line), match.start() + 32)
        columns.append(WeekColumn(resolved, match.start(), next_start))
        last_date = resolved + timedelta(days=1)
    return columns


def clean_layout_cell(value: str) -> str:
    value = value.strip()
    value = re.sub(r"\bW\s+ed\b", "Wed", value)
    value = re.sub(r"\bO\s+n-", "On-", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def is_print_metadata(line: str) -> bool:
    return bool(
        re.fullmatch(
            r"\d{1,2}/\d{1,2}/\d{2,4},\s*\d{1,2}:\d{2}\s*(?:am|pm)\s+\S+",
            line.strip(),
            flags=re.IGNORECASE,
        )
    )


def strip_parenthetical_date(value: str) -> str:
    return re.sub(r"\(\s*\d{1,2}/\d{1,2}(?:/\d{2,4})?\s*\)", "", value).strip()


def parse_grid_column_events(
    column_lines: Sequence[str],
    event_date: date,
    default_duration_minutes: int,
    timed_only: bool,
) -> List[CalendarEvent]:
    events: List[CalendarEvent] = []
    title_parts: List[str] = []

    for raw_line in column_lines:
        cell = clean_layout_cell(raw_line)
        if not cell or looks_like_noise(cell) or DATE_RANGE_RE.fullmatch(cell) or is_print_metadata(cell):
            continue

        time_match = find_time(cell, event_date, default_duration_minutes)
        if not time_match:
            if meaningful_title(cell):
                title_parts.append(cell)
            continue

        inline_title = clean_title(strip_parenthetical_date(strip_span(cell, time_match.start, time_match.end)))
        title = clean_title(" ".join(title_parts)) if title_parts else inline_title
        title = strip_parenthetical_date(title)
        title, location = split_inline_location(title)
        title_parts = []

        if not title or not meaningful_title(title):
            continue

        events.append(
            CalendarEvent(
                title=title,
                start_date=event_date,
                start_time=time_match.start_time,
                end_date=event_date + timedelta(days=1) if time_match.end_is_next_day else event_date,
                end_time=time_match.end_time,
                all_day=False,
                location=location or default_location_for_title(title),
                source_line=cell,
            )
        )

    if title_parts and not timed_only:
        title = clean_title(" ".join(title_parts))
        title, location = split_inline_location(title)
        if title and meaningful_title(title):
            events.append(
                CalendarEvent(
                    title=title,
                    start_date=event_date,
                    all_day=True,
                    location=location or default_location_for_title(title),
                    source_line=" ".join(column_lines),
                )
            )

    return events


def parse_layout_grid_events(
    text: str,
    default_year: int,
    date_order: str,
    default_duration_minutes: int,
    timed_only: bool,
) -> List[CalendarEvent]:
    del date_order  # Grid headers are explicit month/day values.

    date_range = find_calendar_date_range(text)
    events: List[CalendarEvent] = []
    current_columns: Optional[List[WeekColumn]] = None
    column_lines: List[List[str]] = []
    last_grid_date: Optional[date] = None

    def flush_columns() -> None:
        nonlocal column_lines
        if not current_columns:
            return
        for column, lines in zip(current_columns, column_lines):
            events.extend(parse_grid_column_events(lines, column.value, default_duration_minutes, timed_only))
        column_lines = []

    for raw_line in text.splitlines():
        header_columns = parse_week_grid_header(raw_line, default_year, date_range, last_grid_date)
        if header_columns:
            flush_columns()
            current_columns = header_columns
            column_lines = [[] for _ in current_columns]
            last_grid_date = header_columns[-1].value + timedelta(days=1)
            continue

        if not current_columns or not raw_line.strip():
            continue

        stripped_line = raw_line.strip()
        if stripped_line.startswith("--- Page") or DATE_RANGE_RE.fullmatch(stripped_line) or is_print_metadata(stripped_line):
            continue
        if "://" in stripped_line:
            continue

        for index, column in enumerate(current_columns):
            if len(raw_line) <= column.start:
                continue
            segment = raw_line[column.start : column.end]
            if segment.strip():
                column_lines[index].append(segment)

    flush_columns()
    return events


def split_inline_location(title: str) -> Tuple[str, str]:
    match = INLINE_LOCATION_RE.search(title)
    if not match:
        return title, ""
    location = clean_title(match.group("location"))
    return clean_title(title[: match.start()]), location


def append_description(event: CalendarEvent, value: str) -> None:
    value = value.strip()
    if not value:
        return
    event.description = f"{event.description}\n{value}".strip() if event.description else value


def parse_events(
    text: str,
    default_year: int,
    date_order: str = "mdy",
    default_duration_minutes: int = 60,
    timed_only: bool = False,
) -> List[CalendarEvent]:
    grid_events = parse_layout_grid_events(
        text,
        default_year=default_year,
        date_order=date_order,
        default_duration_minutes=default_duration_minutes,
        timed_only=timed_only,
    )
    if grid_events:
        return merge_continuation_lines(grid_events)

    lines = normalize_text(text)
    events: List[CalendarEvent] = []
    current_date: Optional[date] = None
    pending_time: Optional[Tuple[date, TimeMatch, str]] = None

    for line in lines:
        if looks_like_noise(line):
            continue

        location_match = LOCATION_LABEL_RE.match(line)
        if location_match and events:
            events[-1].location = location_match.group("value").strip()
            continue

        description_match = DESCRIPTION_LABEL_RE.match(line)
        if description_match and events:
            append_description(events[-1], description_match.group("value"))
            continue

        date_match = find_date(line, default_year, date_order)
        if date_match and is_date_header(line, date_match):
            current_date = date_match.value
            pending_time = None
            continue

        event_date = date_match.value if date_match else current_date
        if not event_date:
            continue

        working_line = strip_span(line, date_match.start, date_match.end) if date_match else line
        time_match = find_time(working_line, event_date, default_duration_minutes)

        if pending_time and not time_match and not date_match:
            pending_date, pending_clock, source_line = pending_time
            title, location = split_inline_location(clean_title(working_line))
            if title and meaningful_title(title):
                events.append(
                    CalendarEvent(
                        title=title,
                        start_date=pending_date,
                        start_time=pending_clock.start_time,
                        end_date=pending_date + timedelta(days=1) if pending_clock.end_is_next_day else pending_date,
                        end_time=pending_clock.end_time,
                        all_day=False,
                        location=location or default_location_for_title(title),
                        source_line=f"{source_line} {line}".strip(),
                    )
                )
                pending_time = None
                continue

        if time_match:
            title = clean_title(strip_span(working_line, time_match.start, time_match.end))
            if not title or not meaningful_title(title):
                pending_time = (event_date, time_match, line)
                continue
            title, location = split_inline_location(title)
            if not title or not meaningful_title(title):
                pending_time = (event_date, time_match, line)
                continue
            events.append(
                CalendarEvent(
                    title=title,
                    start_date=event_date,
                    start_time=time_match.start_time,
                    end_date=event_date + timedelta(days=1) if time_match.end_is_next_day else event_date,
                    end_time=time_match.end_time,
                    all_day=False,
                    location=location or default_location_for_title(title),
                    source_line=line,
                )
            )
            pending_time = None
            continue

        if timed_only:
            continue

        title = clean_title(working_line)
        if not title or not meaningful_title(title):
            continue

        if date_match or line.startswith(("-", "*", "•")) or TITLE_LABEL_RE.match(line):
            title, location = split_inline_location(title)
            if title and meaningful_title(title):
                events.append(
                    CalendarEvent(
                        title=title,
                        start_date=event_date,
                        all_day=True,
                        location=location or default_location_for_title(title),
                        source_line=line,
                    )
                )

    return merge_continuation_lines(events)


def merge_continuation_lines(events: List[CalendarEvent]) -> List[CalendarEvent]:
    """Merge obvious duplicate all-day entries produced by repeated source lines."""
    seen = set()
    merged: List[CalendarEvent] = []
    for event in events:
        key = (
            event.title.lower(),
            event.start_date,
            event.start_time,
            event.end_time,
            event.location.lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        merged.append(event)
    return merged


def ics_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
    )


def fold_ics_line(line: str) -> str:
    if len(line) <= 74:
        return line
    chunks = [line[:74]]
    rest = line[74:]
    while rest:
        chunks.append(" " + rest[:73])
        rest = rest[73:]
    return "\r\n".join(chunks)


def format_ics_date(value: date) -> str:
    return value.strftime("%Y%m%d")


def format_ics_datetime(value_date: date, value_time: time) -> str:
    return datetime.combine(value_date, value_time).strftime("%Y%m%dT%H%M%S")


def stable_uid(event: CalendarEvent, domain: str = "calendar-pdf-import.local") -> str:
    payload = "|".join(
        [
            event.title,
            event.start_date.isoformat(),
            event.start_time.isoformat() if event.start_time else "",
            event.location,
            event.source_line,
        ]
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    return f"{digest}@{domain}"


def build_description(event: CalendarEvent, source_pdf: Optional[Path]) -> str:
    parts = []
    if event.description:
        parts.append(event.description)
    if source_pdf:
        parts.append(f"Imported from: {source_pdf.name}")
    if event.source_line:
        parts.append(f"Source text: {event.source_line}")
    return "\n".join(parts)


def events_to_ics(events: Sequence[CalendarEvent], calendar_name: str, timezone_id: Optional[str], source_pdf: Optional[Path]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Calendar PDF Importer//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{ics_escape(calendar_name)}",
    ]
    if timezone_id:
        lines.append(f"X-WR-TIMEZONE:{ics_escape(timezone_id)}")

    for event in events:
        lines.extend(["BEGIN:VEVENT", f"UID:{stable_uid(event)}", f"DTSTAMP:{now}"])
        if event.all_day:
            lines.append(f"DTSTART;VALUE=DATE:{format_ics_date(event.start_date)}")
            lines.append(f"DTEND;VALUE=DATE:{format_ics_date(event.effective_end_date)}")
        else:
            if not event.start_time or not event.end_time:
                continue
            prefix = f";TZID={timezone_id}" if timezone_id else ""
            lines.append(f"DTSTART{prefix}:{format_ics_datetime(event.start_date, event.start_time)}")
            lines.append(f"DTEND{prefix}:{format_ics_datetime(event.effective_end_date, event.end_time)}")

        lines.append(f"SUMMARY:{ics_escape(event.title)}")
        if event.location:
            lines.append(f"LOCATION:{ics_escape(event.location)}")
        description = build_description(event, source_pdf)
        if description:
            lines.append(f"DESCRIPTION:{ics_escape(description)}")
        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")
    return "\r\n".join(fold_ics_line(line) for line in lines) + "\r\n"


def print_preview(events: Sequence[CalendarEvent]) -> None:
    if not events:
        print("No events found.")
        return

    for index, event in enumerate(events, start=1):
        if event.all_day:
            when = f"{event.start_date.isoformat()} all day"
        else:
            start = event.start_time.strftime("%H:%M") if event.start_time else "??:??"
            end_date = event.effective_end_date
            end = event.end_time.strftime("%H:%M") if event.end_time else "??:??"
            suffix = f" -> {end_date.isoformat()} {end}" if end_date != event.start_date else f"-{end}"
            when = f"{event.start_date.isoformat()} {start}{suffix}"
        location = f" @ {event.location}" if event.location else ""
        print(f"{index:3}. {when} | {event.title}{location}")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert a text-based calendar PDF printout into an .ics calendar import file."
    )
    parser.add_argument("input_pdf", type=Path, help="Calendar printout PDF to read.")
    parser.add_argument(
        "output_ics",
        type=Path,
        nargs="?",
        help="Output .ics file. Defaults to the input PDF name with an .ics extension.",
    )
    parser.add_argument(
        "--year",
        type=int,
        default=date.today().year,
        help="Year to use when the PDF lists dates without a year. Defaults to the current year.",
    )
    parser.add_argument(
        "--date-order",
        choices=("mdy", "dmy"),
        default="mdy",
        help="How to read numeric dates like 09/10. Defaults to mdy.",
    )
    parser.add_argument(
        "--timezone",
        default=os.environ.get("TZ", "America/Winnipeg"),
        help="TZID to write for timed events. Use --floating-times to omit TZID.",
    )
    parser.add_argument(
        "--floating-times",
        action="store_true",
        help="Write timed events without a timezone identifier so the calendar app imports them as local times.",
    )
    parser.add_argument(
        "--default-duration",
        type=int,
        default=60,
        help="Minutes to use when an event has a start time but no end time. Defaults to 60.",
    )
    parser.add_argument(
        "--timed-only",
        action="store_true",
        help="Only import events with recognizable times; skip all-day event candidates.",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Print detected events before writing the .ics file.",
    )
    parser.add_argument(
        "--dump-text",
        type=Path,
        help="Optional path to save the extracted PDF text for debugging parser issues.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    input_pdf = args.input_pdf
    if not input_pdf.exists():
        print(f"Input PDF not found: {input_pdf}", file=sys.stderr)
        return 2

    output_ics = args.output_ics or input_pdf.with_suffix(".ics")
    try:
        text = extract_pdf_text(input_pdf)
        if args.dump_text:
            args.dump_text.write_text(text, encoding="utf-8")

        events = parse_events(
            text,
            default_year=args.year,
            date_order=args.date_order,
            default_duration_minutes=args.default_duration,
            timed_only=args.timed_only,
        )

        if args.preview:
            print_preview(events)

        if not events:
            print(
                "No events were detected. Try --dump-text extracted.txt to inspect the PDF text, "
                "or OCR the PDF if it is scanned.",
                file=sys.stderr,
            )
            return 1

        timezone_id = None if args.floating_times else args.timezone
        calendar_name = input_pdf.stem.replace("_", " ").replace("-", " ").strip() or "Imported Calendar"
        ics_text = events_to_ics(events, calendar_name, timezone_id, input_pdf)
        with output_ics.open("w", encoding="utf-8", newline="") as handle:
            handle.write(ics_text)
        print(f"Wrote {len(events)} event(s) to {output_ics}")
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
