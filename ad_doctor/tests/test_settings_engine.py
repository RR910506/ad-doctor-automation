from __future__ import annotations

import json
import unittest

from src.settings_engine import (
    DEFAULT_CONTENT_FRAMEWORK,
    parse_content_framework_prompt,
    serialize_content_framework_prompt,
)


class ContentFrameworkPromptTests(unittest.TestCase):
    def test_default_framework_round_trips_through_prompt(self) -> None:
        prompt = serialize_content_framework_prompt(DEFAULT_CONTENT_FRAMEWORK)
        parsed = parse_content_framework_prompt(prompt)

        self.assertEqual(parsed["framework_name"], DEFAULT_CONTENT_FRAMEWORK["framework_name"])
        self.assertEqual(
            json.loads(parsed["content_pillars"]),
            json.loads(DEFAULT_CONTENT_FRAMEWORK["content_pillars"]),
        )
        self.assertEqual(
            json.loads(parsed["carousel_structure"]),
            json.loads(DEFAULT_CONTENT_FRAMEWORK["carousel_structure"]),
        )

    def test_missing_prompt_sections_keep_saved_fallback(self) -> None:
        fallback = dict(DEFAULT_CONTENT_FRAMEWORK)
        fallback["tone_rules"] = "Saved custom tone."

        parsed = parse_content_framework_prompt("# Minimal\n\n## Brand Worldview\nDiagnose first.", fallback)

        self.assertEqual(parsed["framework_name"], "Minimal")
        self.assertEqual(parsed["brand_worldview"], "Diagnose first.")
        self.assertEqual(parsed["tone_rules"], "Saved custom tone.")


if __name__ == "__main__":
    unittest.main()
