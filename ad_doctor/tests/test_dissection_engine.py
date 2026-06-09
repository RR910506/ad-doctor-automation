import sys
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.dissection_engine import build_dissection_markdown


class DissectionEngineTest(unittest.TestCase):
    def test_build_dissection_markdown_includes_references(self) -> None:
        markdown = build_dissection_markdown(
            {
                "topic": "Nike launches AI-powered Olympics campaign",
                "brand_details": "Nike",
                "campaign_name": "To be confirmed",
                "agency_involved": "Not confirmed",
                "campaign_objective": "Build Olympic attention.",
                "strategic_insight": "Athlete data can fuel personalization.",
                "creative_idea": "AI-powered film.",
                "execution": "Digital-first launch.",
                "media_platform_usage": ["YouTube"],
                "audience": "Sports fans",
                "market_context": "Olympics",
                "performance_data": "No performance data found.",
                "source_urls": ["https://example.com/nike-ai"],
            },
            "# Placeholder",
        )

        self.assertIn("Campaign Dissection", markdown)
        self.assertIn("https://example.com/nike-ai", markdown)
