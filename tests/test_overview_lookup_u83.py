"""Offline contract checks for report-local ledger lookup reuse."""
import copy
import unittest
from unittest.mock import patch

from video_publisher import overview


def publication(key, platform="x", work="work"):
    return {"key": key, "platform": platform, "data": {"receipt": {"id": work}}}


def pending(**fields):
    return {"state": "EDIT_SUBMITTING", **fields}


class EditLookupTests(unittest.TestCase):
    def render(self, rows, edits, adopted=()):
        def receipts(folder):
            return iter(adopted if folder == "operations/adopted" else edits)
        with patch.object(overview, "saved_receipts", side_effect=receipts):
            return list(overview.edit_rows(rows))

    def test_original_first_match_wins_even_when_work_precedes_exact_key(self):
        edits = [("a", pending(key="key", platform="x", id="work")),
                 ("b", pending(key="key", platform="x", id="work"))]
        rows = [publication("work-first"), publication("key", work="else")]
        for ordered, expected in ((rows, "work-first"), (rows[::-1], "key")):
            result = self.render(ordered, edits)
            self.assertEqual([f"vpub edit {expected} FILE --verify-only"] * 2,
                             [r["next_command"] for r in result])

    def test_query_scope_includes_key_platform_and_case_sensitive_work(self):
        rows = [publication("a", "x", "Abc"), publication("b", "instagram", "Abc"),
                publication("c", "x", "abc")]
        edits = [(str(i), pending(platform=p, id=w)) for i, (p, w) in enumerate(
            [("x", "Abc"), ("instagram", "Abc"), ("x", "abc"), ("x", "missing")])]
        result = self.render(rows, edits)
        self.assertEqual(["vpub edit a FILE --verify-only", "vpub edit b FILE --verify-only",
                          "vpub edit c FILE --verify-only", None], [r["next_command"] for r in result])

    def test_hits_and_misses_scan_once_but_keep_all_receipts(self):
        class CountingRow(dict):
            reads = 0
            def __getitem__(self, key):
                if key == "data":
                    type(self).reads += 1
                return super().__getitem__(key)
        rows = [CountingRow(publication(str(i), work=str(i))) for i in range(40)]
        edits = [(str(i), pending(platform="x", id="39" if i % 2 else "missing",
                                 at=str(i))) for i in range(30)]
        result = self.render(rows, edits)
        self.assertEqual(80, CountingRow.reads)
        self.assertEqual(30, len(result))
        self.assertEqual([str(i) for i in range(30)], [r["at"] for r in result])

    def test_next_report_uses_new_ledger_and_receipt_state(self):
        edits = [("a", pending(platform="x", id="work"))]
        self.assertIsNone(self.render([], edits)[0]["next_command"])
        self.assertEqual("vpub edit new FILE --verify-only",
                         self.render([publication("new")], edits)[0]["next_command"])
        self.assertEqual([], self.render([publication("new")], [("a", {"state": "EDIT_VERIFIED"})]))

    def test_unhashable_fields_keep_original_comparison_behavior(self):
        rows = [publication("key")]
        edits = [("a", pending(id=[], platform={})),
                 ("b", pending(key="key", id=["odd"], platform={}))]
        result = self.render(rows, edits)
        self.assertEqual([None, "vpub edit key FILE --verify-only"], [r["next_command"] for r in result])
        # adopted.get already rejects an unhashable saved key in the baseline.
        with self.assertRaises(TypeError):
            self.render(rows, [("a", pending(key=[]))])

    def test_adopted_fallback_and_input_values_are_unchanged(self):
        rows = [publication("other", work="else")]
        edits = [("a", pending(platform="instagram", id="Abc")),
                 ("b", pending(platform="instagram", id="Abc"))]
        adopted = [("saved", {"platform": "instagram", "work_id": "Abc", "identity": "owner"})]
        before = copy.deepcopy((rows, edits, adopted))
        result = self.render(rows, edits, adopted)
        self.assertEqual(["vpub edit-work instagram Abc FILE --verify-only"] * 2,
                         [r["next_command"] for r in result])
        self.assertEqual(before, (rows, edits, adopted))

    def test_lookup_does_not_evaluate_rows_after_first_match(self):
        rows = [publication("key"), {"key": "broken", "platform": "x", "data": None}]
        result = self.render(rows, [("a", pending(key="key", platform="x", id="work"))] * 2)
        self.assertEqual(2, len(result))
