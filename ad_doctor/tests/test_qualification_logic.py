import sys
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.qualification_engine import calculate_topic_score, minimum_required_sources


class QualificationLogicTest(unittest.TestCase):
    def test_minimum_required_sources_uses_fixed_three_site_gate(self) -> None:
        self.assertEqual(minimum_required_sources(22), 3)
        self.assertEqual(minimum_required_sources(4), 3)
        self.assertEqual(minimum_required_sources(2), 3)
        self.assertEqual(minimum_required_sources(0), 0)

    def test_extra_15_percent_is_priority_boost_only(self) -> None:
        score = calculate_topic_score(
            topic_id=1,
            active_source_count=20,
            unique_source_coverage_count=8,
            scoring_inputs={
                "coverages": [
                    {"credibility_tier": 3, "article_text_length": 2000},
                    {"credibility_tier": 2, "article_text_length": 1000},
                ]
            },
        )

        self.assertEqual(score["minimum_required_sources"], 3)
        self.assertEqual(score["priority_boost_applied"], 1)
        self.assertAlmostEqual(score["coverage_percentage"], 0.4)
