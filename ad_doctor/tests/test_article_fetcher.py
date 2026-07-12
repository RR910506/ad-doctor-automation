import sys
import unittest
from pathlib import Path
from unittest import mock


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.article_fetcher import dedupe_sources, resolve_source_parallelism


class ArticleFetcherSettingsTest(unittest.TestCase):
    def test_dedupe_sources_removes_duplicate_websites(self) -> None:
        sources = [
            {"source_name": "Afaqs", "website_url": "https://afaqs.com"},
            {"source_name": "Afaqs Duplicate", "website_url": "https://www.afaqs.com/"},
            {"source_name": "The Drum", "website_url": "https://thedrum.com"},
        ]

        deduped = dedupe_sources(sources)

        self.assertEqual([source["source_name"] for source in deduped], ["Afaqs", "The Drum"])

    def test_parallelism_defaults_to_four_and_caps_at_six(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(resolve_source_parallelism(None, 32), 4)
        with mock.patch.dict("os.environ", {"AD_DOCTOR_SOURCE_PARALLELISM": "99"}, clear=True):
            self.assertEqual(resolve_source_parallelism(None, 32), 6)


if __name__ == "__main__":
    unittest.main()
