import sys
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.research_engine import build_approved_source_only_research_payload, build_local_dossier
import src.perplexity_research as perplexity_research


class ResearchEngineTest(unittest.TestCase):
    def test_build_local_dossier_includes_source_urls(self) -> None:
        dossier = build_local_dossier(
            {
                "locked_topic": {
                    "locked_topic": "Nike launches AI-powered Olympics campaign",
                },
                "articles": [
                    {
                        "source_name": "Ad Age",
                        "article_url": "https://example.com/nike-ai",
                        "headline": "Nike launches AI-powered Olympics campaign",
                        "meta_description": "Nike launches an AI campaign on YouTube and Instagram.",
                        "article_text": "Nike launches an AI campaign on YouTube and Instagram.",
                    }
                ],
            }
        )

        self.assertEqual(dossier["brand_details"], "Nike")
        self.assertIn("https://example.com/nike-ai", dossier["source_urls"])
        self.assertIn("YouTube", dossier["media_platform_usage"])

    def test_build_local_dossier_excludes_irrelevant_same_brand_articles(self) -> None:
        dossier = build_local_dossier(
            {
                "locked_topic": {
                    "locked_topic": "Coca-Cola crowned eCommerce Brand of the Year at Asia eCommerce Awards 2025",
                },
                "articles": [
                    {
                        "source_name": "Marketing Interactive",
                        "article_url": "https://example.com/coca-cola-ecommerce-award",
                        "headline": "Coca-Cola crowned eCommerce Brand of the Year at Asia eCommerce Awards 2025",
                        "meta_description": "Coca-Cola wins an ecommerce award.",
                        "article_text": "Coca-Cola wins an ecommerce award.",
                    },
                    {
                        "source_name": "Ad Age",
                        "article_url": "https://example.com/coca-cola-media-review",
                        "headline": "Coca-Cola media, data and tech agency review pits WPP against Publicis",
                        "meta_description": "Coca-Cola launches a media review.",
                        "article_text": "Coca-Cola launches a media review.",
                    },
                ],
            }
        )

        self.assertIn("https://example.com/coca-cola-ecommerce-award", dossier["source_urls"])
        self.assertNotIn("https://example.com/coca-cola-media-review", dossier["source_urls"])

    def test_perplexity_research_falls_back_without_api_key(self) -> None:
        original_api_key = perplexity_research.PERPLEXITY_API_KEY
        try:
            perplexity_research.PERPLEXITY_API_KEY = ""
            result = perplexity_research.research_topic_with_perplexity(
                "Nike launches AI-powered Olympics campaign",
                [
                    {
                        "source_name": "Ad Age",
                        "headline": "Nike launches AI-powered Olympics campaign",
                        "article_url": "https://example.com/nike-ai",
                    }
                ],
            )
        finally:
            perplexity_research.PERPLEXITY_API_KEY = original_api_key

        self.assertEqual(result["status"], "unavailable")
        self.assertIn("summary", result)
        self.assertIn("source_links", result)

    def test_approved_source_only_payload_does_not_require_perplexity(self) -> None:
        payload = build_approved_source_only_research_payload(
            "Nike launches AI-powered Olympics campaign",
            [
                {
                    "source_name": "Ad Age",
                    "article_url": "https://example.com/nike-ai",
                },
                {
                    "source_name": "The Drum",
                    "article_url": "https://example.com/nike-ai-2",
                },
            ],
        )

        self.assertEqual(payload["status"], "approved_source_only")
        self.assertEqual(payload["model"], "local-approved-source-synthesis")
        self.assertIn("External AI research is disabled", payload["summary"])
        self.assertIn("https://example.com/nike-ai", payload["source_links"])
