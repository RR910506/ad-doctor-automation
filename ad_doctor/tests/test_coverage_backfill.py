import sys
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.article_parser import ParsedArticle, extract_article_link_candidates
from src.coverage_backfill import (
    extract_sitemap_locs,
    headline_from_url,
    is_candidate_promising,
    build_source_search_urls,
    is_valid_backfill_match,
    rank_child_sitemaps,
)


class CoverageBackfillTest(unittest.TestCase):
    def test_search_urls_stay_on_approved_source_domain(self) -> None:
        urls = build_source_search_urls(
            "https://example.com/advertising",
            "Coca-Cola launches global media review as WPP and Publicis go head to head",
        )

        self.assertTrue(urls)
        self.assertTrue(all(url.startswith("https://example.com/") for url in urls))

    def test_link_candidates_preserve_anchor_headline(self) -> None:
        html = """
        <a href="/news/coca-cola-launches-global-media-review">
            Coca-Cola launches global media review as WPP and Publicis go head to head
        </a>
        """

        candidates = extract_article_link_candidates("https://example.com", html)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(
            candidates[0]["headline"],
            "Coca-Cola launches global media review as WPP and Publicis go head to head",
        )

    def test_valid_backfill_match_requires_same_news_event(self) -> None:
        topic = "Coca-Cola launches global media review as WPP and Publicis go head to head"
        matching = ParsedArticle(
            article_url="https://example.com/news/coca-cola-media-review-wpp-publicis",
            headline="Coca-Cola media, data and tech agency review pits WPP against Publicis",
            published_date=None,
            author=None,
            meta_description=None,
            article_text=None,
        )
        unrelated = ParsedArticle(
            article_url="https://example.com/news/coca-cola-turns-bottle-into-chopsticks",
            headline="Coca-Cola turns its iconic bottle into chopsticks for Southeast Asia",
            published_date=None,
            author=None,
            meta_description=None,
            article_text=None,
        )

        self.assertTrue(is_valid_backfill_match(topic, matching))
        self.assertFalse(is_valid_backfill_match(topic, unrelated))

    def test_sitemap_helpers_find_promising_article_urls(self) -> None:
        topic = "Coca-Cola launches global media review as WPP and Publicis go head to head"
        xml_text = """
        <urlset>
            <url><loc>https://example.com/agencies/coca-cola-pits-incumbent-wpp-against-publicis-in-us-media-review/</loc></url>
        </urlset>
        """

        locs = extract_sitemap_locs(xml_text)
        headline = headline_from_url(locs[0])

        self.assertEqual(len(locs), 1)
        self.assertIn("coca cola", headline)
        self.assertTrue(is_candidate_promising(topic, {"article_url": locs[0], "headline": headline}))

    def test_child_sitemaps_prioritize_relevant_sections(self) -> None:
        ranked = rank_child_sitemaps(
            [
                "https://example.com/events/sitemap.xml",
                "https://example.com/agencies/sitemap.xml",
                "https://example.com/resource-library/sitemap.xml",
            ]
        )

        self.assertEqual(ranked[0], "https://example.com/events/sitemap.xml")
