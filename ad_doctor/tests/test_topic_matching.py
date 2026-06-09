import unittest

from src.topic_matcher import find_matching_topic, is_same_news_event


class TopicMatchingPlaceholderTest(unittest.TestCase):
    def test_phase_one_placeholder(self) -> None:
        self.assertTrue(True)

    def test_find_matching_topic_uses_aliases_for_transitive_event_matching(self) -> None:
        existing = [
            {
                "id": 1,
                "exact_topic": "Coca-Cola hands Publicis a shot at WPP's global media crown",
                "representative_headline": "Coca-Cola hands Publicis a shot at WPP's global media crown",
                "aliases": [
                    "Coca-Cola launches global media review as WPP and Publicis go head to head",
                ],
            }
        ]

        match = find_matching_topic(
            "Coca-Cola media, data and tech agency review pits WPP against Publicis",
            existing,
        )

        self.assertIsNotNone(match)
        self.assertEqual(match["id"], 1)

    def test_named_event_year_matches_related_event_coverage(self) -> None:
        match = find_matching_topic(
            "7 New Things at Cannes Lions 2026",
            [
                {
                    "id": 10,
                    "exact_topic": "Cannes Lions 2026 schedule-events for CMOs, creatives, ad tech leaders and more",
                    "representative_headline": "Cannes Lions 2026 schedule-events for CMOs, creatives, ad tech leaders and more",
                    "aliases": [],
                }
            ],
        )

        self.assertIsNotNone(match)
        self.assertEqual(match["id"], 10)

    def test_cannes_year_without_lions_does_not_force_match(self) -> None:
        self.assertFalse(
            is_same_news_event(
                "Cannes Lions 2026 schedule-events for CMOs, creatives, ad tech leaders and more",
                "Sport Beach to return to Cannes 2026 with new strategic partner Boardroom",
            )
        )

    def test_cannes_contenders_does_not_match_schedule_topic(self) -> None:
        self.assertFalse(
            is_same_news_event(
                "Cannes Lions 2026 schedule, events and industry coverage",
                "Cannes Contenders 2026: The UK",
            )
        )

    def test_cannes_film_festival_does_not_match_cannes_lions(self) -> None:
        self.assertFalse(
            is_same_news_event(
                "Cannes Lions 2026 schedule-events for CMOs, creatives, ad tech leaders and more",
                "Empty red carpet makes a statement as the Cannes Film Festival gets underway",
            )
        )

    def test_appointment_synonyms_match_same_people_and_brand(self) -> None:
        self.assertTrue(
            is_same_news_event(
                "Duolingo English Test appoints Divya Rao as Marketing Lead",
                "Divya Rao joins Duolingo English Test as marketing head",
            )
        )

    def test_review_rewrite_matches_same_entities(self) -> None:
        self.assertTrue(
            is_same_news_event(
                "Coca-Cola hands Publicis a shot at WPP's global media crown",
                "Coca-Cola media, data and tech agency review pits WPP against Publicis",
            )
        )

    def test_coca_cola_award_does_not_match_media_review(self) -> None:
        self.assertFalse(
            is_same_news_event(
                "Coca-Cola crowned 'eCommerce Brand of the Year' at Asia eCommerce Awards 2025",
                "Coca-Cola launches global media review as WPP and Publicis go head to head",
            )
        )

    def test_coca_cola_award_matches_same_award_story(self) -> None:
        self.assertTrue(
            is_same_news_event(
                "Coca-Cola crowned 'eCommerce Brand of the Year' at Asia eCommerce Awards 2025",
                "The Coca-Cola Company crowned eCommerce Brand of the Year at the Asia eCommerce Awards 2025",
            )
        )

    def test_same_brand_different_event_does_not_match(self) -> None:
        self.assertFalse(
            is_same_news_event(
                "Coca-Cola hands Publicis a shot at WPP's global media crown",
                "Coca-Cola turns its iconic bottle into chopsticks for Southeast Asia",
            )
        )

    def test_conflicting_event_years_do_not_match(self) -> None:
        self.assertFalse(
            is_same_news_event(
                "World Cup 2026 campaign attracts advertisers",
                "World Cup 2022 campaign attracts advertisers",
            )
        )

    def test_broad_world_cup_context_does_not_merge_different_brand_campaigns(self) -> None:
        self.assertFalse(
            is_same_news_event(
                "Ad of the Day: Nike upstages Adidas’s World Cup",
                "Motorola unveils Football is Calling FIFA World Cup campaign",
            )
        )

    def test_report_year_and_country_are_not_enough_to_match(self) -> None:
        self.assertFalse(
            is_same_news_event(
                "Ad Age Brand Report 2025—biggest advertisers in the US and world",
                "FCB New York named top US agency in Campaign Red’s 2025 Global Awards Rankings",
            )
        )

    def test_cannes_film_festival_does_not_merge_with_cannes_lions(self) -> None:
        self.assertFalse(
            is_same_news_event(
                "What are Indian creators doing at the 2023 Cannes Film Festival?",
                "What gave me creative jitters at Cannes Lions",
            )
        )

    def test_ads_of_week_listicle_is_not_same_as_specific_nike_story(self) -> None:
        self.assertFalse(
            is_same_news_event(
                "Ad of the Day: Nike upstages Adidas’s World Cup",
                "Ads of the Week: 12 Campaigns That Caught Our Eye, From Nike to McDonald’s",
            )
        )
