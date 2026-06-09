import sys
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.content_engine import build_blog_draft, build_social_draft, infer_content_pillar


class ContentEngineTest(unittest.TestCase):
    def test_cannes_topic_uses_culture_scan_even_with_agency_context(self) -> None:
        self.assertEqual(
            infer_content_pillar(
                "Cannes Lions 2026 schedule, events and industry coverage",
                {},
                "Agency planning and brand implications appear in the analysis.",
            ),
            "Culture Scan",
        )

    def test_build_blog_draft_includes_references(self) -> None:
        draft = build_blog_draft(
            {
                "topic": "Nike launches AI-powered Olympics campaign",
                "brand_details": "Nike",
                "campaign_name": "To be confirmed",
                "media_platform_usage": ["YouTube"],
                "market_context": "Olympics",
                "strategic_insight": "Athlete data matters.",
                "creative_idea": "AI-powered film.",
                "execution": "Digital launch.",
                "source_urls": ["https://example.com/nike-ai"],
            },
            "Dissection notes",
            "# Placeholder",
        )

        self.assertIn("Nike launches", draft)
        self.assertIn("https://example.com/nike-ai", draft)

    def test_build_social_draft_uses_ad_doctor_carousel_framework(self) -> None:
        draft = build_social_draft(
            {
                "topic": "Nike launches AI-powered Olympics campaign",
                "brand_details": "Nike",
                "campaign_objective": "Build Olympics attention through athlete storytelling.",
                "market_context": "Olympics marketing is becoming more data-led and culturally competitive.",
                "strategic_insight": "Athlete data matters.",
                "creative_idea": "AI-powered film.",
                "execution": "Digital launch.",
                "media_platform_usage": ["YouTube", "Instagram"],
                "audience": "Sports fans and young creators.",
                "performance_data": "No performance data found in stored source coverage.",
                "perplexity_research": {
                    "status": "completed",
                    "summary": "Nike is using AI-led athlete storytelling to build Olympics attention.",
                    "brand_strategy": "Nike is turning athlete data into a creative storytelling device.",
                    "consumer_shift": "Fans increasingly expect sports brands to give them participatory, personalized storytelling.",
                    "business_impact": "data unavailable",
                    "cultural_relevance": "AI culture and Olympics fandom are converging around athlete identity.",
                    "marketing_lesson": "The real lesson is that AI matters only when it sharpens the human story.",
                    "verified_data_points": [],
                    "source_links": ["https://example.com/nike-ai"],
                },
                "source_names": ["Ad Age", "Adweek", "The Drum"],
                "source_urls": [
                    "https://example.com/nike-ai",
                    "https://example.com/nike-ai-2",
                    "https://example.com/nike-ai-3",
                ],
                "source_articles": [
                    {
                        "source_name": "Ad Age",
                        "headline": "Nike launches AI-powered Olympics campaign",
                        "article_url": "https://example.com/nike-ai",
                    },
                    {
                        "source_name": "Adweek",
                        "headline": "Nike launches AI-powered Olympics campaign for athletes",
                        "article_url": "https://example.com/nike-ai-2",
                    },
                    {
                        "source_name": "The Drum",
                        "headline": "Nike launches AI-powered Olympics campaign ahead of the Games",
                        "article_url": "https://example.com/nike-ai-3",
                    },
                ],
            },
            "Dissection notes",
            "# The Ad Doctor Social Media Carousel Framework",
        )

        self.assertIn("Topic Name: Nike launches AI-powered Olympics campaign", draft)
        self.assertIn("Qualification Status: Qualified", draft)
        self.assertIn("Sources Count: 3", draft)
        self.assertIn("Sources Used: Ad Age, Adweek, The Drum", draft)
        self.assertIn("Perplexity Research Summary:", draft)
        self.assertIn("Core Strategic Insight:", draft)
        self.assertIn("Selected Content Pillar:", draft)
        self.assertIn("Carousel Draft:", draft)
        self.assertIn("Slide 1:", draft)
        self.assertIn("Slide 8:", draft)
        self.assertIn("Headline:", draft)
        self.assertIn("Body:", draft)
        self.assertIn("Visual Suggestion:", draft)
        self.assertIn("Bold Words:", draft)
        self.assertIn("Source:", draft)
        self.assertIn("Why Nike Is Bigger Than A Platform Update", draft)
        self.assertIn("What We Think", draft)
        self.assertIn("Ad Doctor's view", draft)
        self.assertIn("Approved coverage from Ad Age, Adweek, The Drum", draft)
        self.assertIn("Caption:", draft)
        self.assertIn("Hashtags:", draft)
        self.assertIn("Designer Notes:", draft)
        self.assertIn("Fact-check Notes:", draft)
        self.assertIn("Risk Notes:", draft)
        self.assertIn("Follow @the_ad_doctor for more marketing breakdowns.", draft)
        self.assertNotIn("On-slide copy", draft)
        self.assertNotIn("Why this slide exists", draft)
        self.assertNotIn("## SECTION", draft)
        self.assertNotIn("Visual References", draft)
        self.assertIn("#TheAdDoctor", draft)
        self.assertIn("#AIMarketing", draft)

    def test_social_draft_rejects_irrelevant_reference_bucket(self) -> None:
        draft = build_social_draft(
            {
                "topic": "Coca-Cola crowned eCommerce Brand of the Year at Asia eCommerce Awards 2025",
                "source_articles": [
                    {
                        "source_name": "Ad Age",
                        "headline": "Coca-Cola media, data and tech agency review pits WPP against Publicis",
                        "article_url": "https://example.com/coca-cola-media-review",
                    },
                    {
                        "source_name": "Campaign India",
                        "headline": "Coca-Cola and Google launch AI-powered experience",
                        "article_url": "https://example.com/coca-cola-google-ai",
                    },
                    {
                        "source_name": "Campaign India",
                        "headline": "FCB India, Famous Innovations, McCann win big at the South Asia Agency of the Year awards",
                        "article_url": "https://www.campaignindia.in/article/fcb-india-famous-innovations-mccann-win-big-at-the-south-asia-agency-of-the-yea/4en2jrqays1dnx7f7ctv46gqdg",
                    },
                    {
                        "source_name": "Branding in Asia",
                        "headline": "About Spotlight | Publishing with Branding in Asia",
                        "article_url": "https://brandinginasia.com/about-spotlight",
                    },
                    {
                        "source_name": "Marketing Interactive",
                        "headline": "Coca-Cola crowned eCommerce Brand of the Year at Asia eCommerce Awards 2025",
                        "article_url": "https://example.com/coca-cola-ecommerce-award",
                    },
                ],
            },
            "Dissection notes",
            "# The Ad Doctor Social Media Carousel Framework",
        )

        self.assertIn("Qualification Status: Not Qualified", draft)
        self.assertIn("fewer than 3 topic-specific references", draft)
        self.assertIn("coca-cola-ecommerce-award", draft)
        self.assertNotIn("coca-cola-media-review", draft)
        self.assertNotIn("coca-cola-google-ai", draft)
        self.assertNotIn("fcb-india-famous-innovations", draft)
        self.assertNotIn("about-spotlight", draft)

    def test_build_social_draft_rejects_under_three_sources(self) -> None:
        draft = build_social_draft(
            {
                "topic": "Single source campaign update",
                "source_names": ["Ad Age"],
                "source_urls": ["https://example.com/single"],
            },
            "Dissection notes",
            "# The Ad Doctor Social Media Carousel Framework",
        )

        self.assertIn("Qualification Status: Not Qualified", draft)
        self.assertIn("fewer than 3", draft)
        self.assertNotIn("Carousel Draft", draft)
