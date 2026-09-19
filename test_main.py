import unittest
from datetime import date, time

from main import CalendarEvent, events_to_ics, parse_events
from web_app import ReviewSession, infer_default_year_from_calendar_text, selected_events_from_form, validate_timezone


GRID_COLUMNS = [0, 32, 64, 96, 129, 161, 193]


def grid_line(cells):
    width = 230
    chars = [" "] * width
    for column_index, value in cells.items():
        start = GRID_COLUMNS[column_index]
        for offset, char in enumerate(value):
            chars[start + offset] = char
    return "".join(chars).rstrip()


class FakeForm:
    def __init__(self, values):
        self.values = values

    def getfirst(self, key, default=None):
        return self.values.get(key, default)


class CalendarPdfImporterTests(unittest.TestCase):
    def test_parses_date_heading_timed_event_and_details(self):
        text = """
        Monday, September 14, 2026
        9:00 AM - 10:30 AM Staff Meeting @ Room 204
        Location: Main Office
        Notes: Bring reports
        """

        events = parse_events(text, default_year=2026)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].title, "Staff Meeting")
        self.assertEqual(events[0].start_date.isoformat(), "2026-09-14")
        self.assertEqual(events[0].start_time.isoformat(), "09:00:00")
        self.assertEqual(events[0].end_time.isoformat(), "10:30:00")
        self.assertEqual(events[0].location, "Main Office")
        self.assertEqual(events[0].description, "Bring reports")

    def test_parses_inline_date_events_and_all_day_event(self):
        text = """
        September 2026
        Sep 15 2pm Parent Meeting
        09/16/2026 School Closed
        """

        events = parse_events(text, default_year=2026)

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].title, "Parent Meeting")
        self.assertFalse(events[0].all_day)
        self.assertEqual(events[1].title, "School Closed")
        self.assertTrue(events[1].all_day)

    def test_writes_ics_events(self):
        events = parse_events("Sep 15 2pm Parent Meeting", default_year=2026)

        ics = events_to_ics(events, "Sample", "America/Winnipeg", None)

        self.assertIn("BEGIN:VCALENDAR", ics)
        self.assertIn("BEGIN:VEVENT", ics)
        self.assertIn("DTSTART;TZID=America/Winnipeg:20260915T140000", ics)
        self.assertIn("SUMMARY:Parent Meeting", ics)

    def test_parses_grid_layout_columns(self):
        text = "\n".join(
            [
                "09/01/2026-04/01/2027",
                grid_line(
                    {
                        0: "Sun 9/20",
                        1: "Mon 9/21",
                        2: "Tue 9/22",
                        3: "Wed 9/23",
                        4: "Thu 9/24",
                        5: "Fri 9/25",
                        6: "Sat 9/26",
                    }
                ),
                grid_line({5: "Hospitalist", 6: "Hospitalist"}),
                grid_line({5: "8:00 am - 5:00 pm", 6: "8:00 am - 5:00 pm"}),
                grid_line({5: "Hospitalist O n-Call"}),
                grid_line({5: "5:00 pm - 8:00 am (09/26)"}),
                grid_line(
                    {
                        0: "Sun 1/3",
                        1: "Mon 1/4",
                        2: "Tue 1/5",
                        3: "Wed 1/6",
                        4: "Thu 1/7",
                        5: "Fri 1/8",
                        6: "Sat 1/9",
                    }
                ),
                grid_line({5: "MUCAM"}),
                grid_line({5: "9:00 am - 4:30 pm"}),
            ]
        )

        events = parse_events(text, default_year=2026)

        self.assertEqual(len(events), 4)
        self.assertEqual(events[0].title, "Hospitalist")
        self.assertEqual(events[0].start_date.isoformat(), "2026-09-25")
        self.assertEqual(
            events[0].location,
            "Boundary Trails Health Centre, Hwy 3 & Hwy 14, Winkler, MB R6W 1H8, Canada",
        )
        self.assertEqual(events[1].title, "Hospitalist On-Call")
        self.assertEqual(events[1].end_date.isoformat(), "2026-09-26")
        self.assertEqual(events[2].start_date.isoformat(), "2026-09-26")
        self.assertEqual(events[3].title, "MUCAM")
        self.assertEqual(events[3].start_date.isoformat(), "2027-01-08")
        self.assertEqual(
            events[3].location,
            "Menzies Medical Centre, 130-30 Stephen Street, Morden, MB R6M 2G3, Canada",
        )

    def test_default_locations_are_written_to_ics(self):
        events = parse_events("Sep 15 2pm Hospitalist\nSep 16 9am MUCAM", default_year=2026)

        ics = events_to_ics(events, "Sample", "America/Winnipeg", None)

        self.assertIn("LOCATION:Boundary Trails Health Centre", ics)
        self.assertIn("LOCATION:Menzies Medical Centre", ics)

    def test_review_form_can_edit_and_deselect_events(self):
        session = ReviewSession(
            token="token",
            created_at=0,
            events=[
                CalendarEvent(
                    title="Hospitalist",
                    start_date=date(2026, 9, 25),
                    start_time=time(8, 0),
                    end_date=date(2026, 9, 25),
                    end_time=time(17, 0),
                    source_line="8:00 am - 5:00 pm",
                ),
                CalendarEvent(
                    title="MUCAM",
                    start_date=date(2026, 10, 22),
                    start_time=time(9, 0),
                    end_date=date(2026, 10, 22),
                    end_time=time(16, 30),
                ),
            ],
            source_filename="test cal.pdf",
            download_filename="test cal.ics",
            calendar_name="test cal",
            timezone_id="America/Winnipeg",
            date_range_label="2026-09-01 to 2027-04-01",
        )
        form = FakeForm(
            {
                "include_0": "on",
                "title_0": "Hospitalist Edited",
                "start_date_0": "2026-09-25",
                "start_time_0": "08:30",
                "end_date_0": "2026-09-25",
                "end_time_0": "17:15",
                "location_0": "Ward",
            }
        )

        events = selected_events_from_form(form, session)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].title, "Hospitalist Edited")
        self.assertEqual(events[0].start_time.isoformat(), "08:30:00")
        self.assertEqual(events[0].end_time.isoformat(), "17:15:00")
        self.assertEqual(events[0].location, "Ward")

    def test_timezone_validation_rejects_invalid_names(self):
        self.assertEqual(validate_timezone("America/Winnipeg"), "America/Winnipeg")
        with self.assertRaises(ValueError):
            validate_timezone("../../bad")

    def test_default_year_is_inferred_from_calendar_range(self):
        text = "09/01/2026-04/01/2027\nSun 9/20 Mon 9/21"

        self.assertEqual(infer_default_year_from_calendar_text(text), 2026)

        with self.assertRaises(ValueError):
            infer_default_year_from_calendar_text("Sun 9/20 Mon 9/21")


if __name__ == "__main__":
    unittest.main()
