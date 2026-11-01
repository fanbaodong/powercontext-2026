"""Fixed acceptance tests for REQ-TAG-001 revision 2; do not edit to pass."""
import json
from pathlib import Path
import sys
import unittest

SAMPLE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SAMPLE))
from tickets import select_tickets


class TicketTagAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.tickets = json.loads((SAMPLE / "tickets.json").read_text(encoding="utf-8"))

    def ids(self, **filters):
        return [item["id"] for item in select_tickets(self.tickets, **filters)]

    def test_single_tag_filters_nonmatching_tickets(self):
        self.assertEqual(self.ids(tags=["security"]), [4, 9])

    def test_all_requested_tags_are_required_not_any(self):
        self.assertEqual(self.ids(tags=["backend", "bug"]), [9])

    def test_normalize_both_requested_and_stored_tags(self):
        self.assertEqual(self.ids(tags=[" SECURITY ", "BACKEND"]), [4, 9])

    def test_ignore_duplicate_and_blank_requested_tags(self):
        self.assertEqual(self.ids(tags=["backend", "", " BACKEND ", "   "]), [1, 4, 9])

    def test_unicode_casefold_not_ascii_only_lower(self):
        self.assertEqual(self.ids(tags=["STRASSE"]), [12])

    def test_unknown_tag_returns_no_tickets(self):
        self.assertEqual(self.ids(tags=["not-a-present-label"]), [])

    def test_apply_combined_status_and_tags_before_page_limit(self):
        self.assertEqual(self.ids(status="open", tags=["security"], limit=1), [9])


if __name__ == "__main__":
    unittest.main()
