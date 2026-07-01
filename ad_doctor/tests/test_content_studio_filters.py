from __future__ import annotations

from pathlib import Path
import sys
import unittest


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))


from app import (
    build_period_options,
    format_content_draft_option,
    group_outputs_by_timestamp,
    output_matches_content_filters,
)


class ContentStudioFilterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.outputs = [
            {
                "id": 1,
                "locked_topic_id": 10,
                "locked_topic": "First topic",
                "run_id": "run_morning",
                "content_type": "social",
                "approval_status": "pending",
                "created_at": "2026-06-23 03:30:00",
            },
            {
                "id": 2,
                "locked_topic_id": 10,
                "locked_topic": "First topic",
                "run_id": "run_morning",
                "content_type": "blog",
                "approval_status": "pending",
                "created_at": "2026-06-23 03:30:08",
            },
            {
                "id": 3,
                "locked_topic_id": 11,
                "locked_topic": "Second topic",
                "run_id": "run_evening",
                "content_type": "social",
                "approval_status": "approved",
                "created_at": "2026-06-23 12:30:00",
            },
        ]

    def test_outputs_are_grouped_by_run_even_when_row_seconds_differ(self) -> None:
        batches = group_outputs_by_timestamp(self.outputs)

        self.assertEqual(len(batches), 2)
        self.assertEqual([len(batch_outputs) for _, batch_outputs in batches], [1, 2])

    def test_day_filter_keeps_every_timestamp_from_that_day(self) -> None:
        period_options = build_period_options(self.outputs, "Day")
        period_key = next(iter(period_options.values()))
        filtered = [
            output
            for output in self.outputs
            if output_matches_content_filters(
                output,
                content_filter="All content",
                approval_filter="All statuses",
                period_view="Day",
                period_key=period_key,
            )
        ]

        self.assertEqual(len(filtered), 3)
        self.assertEqual(len(group_outputs_by_timestamp(filtered)), 2)

    def test_all_timestamp_draft_label_includes_exact_time(self) -> None:
        label = format_content_draft_option(self.outputs[0], include_timestamp=True)

        self.assertIn("09:00:00 AM", label)
        self.assertIn("Social", label)
        self.assertIn("First topic", label)


if __name__ == "__main__":
    unittest.main()
