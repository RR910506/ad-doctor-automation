import sys
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.approval_engine import VALID_APPROVAL_STATUSES
from src.export_engine import build_export_filename, slugify


class ApprovalExportTest(unittest.TestCase):
    def test_approval_statuses_are_manual_review_states(self) -> None:
        self.assertIn("approved", VALID_APPROVAL_STATUSES)
        self.assertIn("rejected", VALID_APPROVAL_STATUSES)
        self.assertIn("pending", VALID_APPROVAL_STATUSES)

    def test_export_filename_is_slugged_markdown(self) -> None:
        filename = build_export_filename(
            {
                "id": 3,
                "locked_topic": "Nike launches AI-powered Olympics campaign",
                "content_type": "blog",
            }
        )

        self.assertEqual(filename, "0003_nike-launches-ai-powered-olympics-campaign_blog.md")

    def test_slugify_falls_back_to_untitled(self) -> None:
        self.assertEqual(slugify("!!!"), "untitled")
