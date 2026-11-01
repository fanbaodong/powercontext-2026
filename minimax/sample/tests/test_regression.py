"""Existing queue behavior that the tag change must preserve."""
import copy
import json
from pathlib import Path
import sys
import unittest

SAMPLE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SAMPLE))
from tickets import select_tickets


class TicketRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tickets = json.loads((SAMPLE / "tickets.json").read_text(encoding="utf-8"))

    def ids(self, **filters):
        return [item["id"] for item in select_tickets(self.tickets, **filters)]

    def test_default_returns_every_ticket_sorted_by_id(self):
        self.assertEqual(self.ids(), [1, 2, 4, 7, 9, 12, 15])

    def test_status_keeps_exact_matching_behavior(self):
        self.assertEqual(self.ids(status="open"), [1, 7, 9, 12, 15])
        self.assertEqual(self.ids(status="OPEN"), [])

    def test_unknown_status_returns_empty(self):
        self.assertEqual(self.ids(status="pending"), [])

    def test_limit_is_applied_after_status_and_sorting(self):
        self.assertEqual(self.ids(status="closed", limit=1), [2])

    def test_zero_limit_returns_empty(self):
        self.assertEqual(self.ids(limit=0), [])

    def test_large_limit_does_not_add_rows(self):
        self.assertEqual(self.ids(limit=100), [1, 2, 4, 7, 9, 12, 15])

    def test_no_requested_tags_preserves_unfiltered_behavior(self):
        for tags in (None, [], ["", "  "]):
            with self.subTest(tags=tags):
                self.assertEqual(self.ids(tags=tags), [1, 2, 4, 7, 9, 12, 15])

    def test_original_input_and_tag_filters_are_not_mutated(self):
        before = copy.deepcopy(self.tickets)
        filters = [" Security ", "backend", "backend"]
        original_filters = filters.copy()
        selected = select_tickets(self.tickets, tags=filters, limit=1)
        self.assertEqual(self.tickets, before)
        self.assertEqual(filters, original_filters)
        self.assertIsNot(selected, self.tickets)

    def test_non_tag_fields_are_preserved(self):
        result = select_tickets(self.tickets, status="closed")
        self.assertEqual(result, [self.tickets[1], self.tickets[3]])

    def test_empty_queue_is_valid(self):
        self.assertEqual(select_tickets([], status="open", tags=["security"]), [])

    def test_invalid_limit_still_raises_value_error(self):
        for limit in (-1, 1.5, True, "2"):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                select_tickets(self.tickets, limit=limit)

    def test_iterable_input_remains_supported(self):
        self.assertEqual(
            [row["id"] for row in select_tickets(iter(self.tickets), status="closed")],
            [2, 4],
        )


if __name__ == "__main__":
    unittest.main()
