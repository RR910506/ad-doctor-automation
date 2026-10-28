from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from src.article_fetcher import FetchSummary
from src.services.publication_collection import collect_publications
from src.db import get_connection
from src.db import initialize_database, upsert_article, upsert_source
from datetime import UTC, datetime, timedelta
from contextlib import closing


class PublicationCollectionTests(TestCase):
    def test_import_full_text_and_empty_result_are_honest(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "collection.db"
            with patch(
                "src.services.publication_collection.fetch_articles",
                return_value=FetchSummary(
                    "run", 30, 0, 0, 0, 30, ["Sources unavailable"]
                ),
            ) as fetch:
                result = collect_publications(database)
                self.assertEqual(result["topics"], [])
                self.assertEqual(result["coverage_status"], "bounded_scan")
                self.assertTrue(fetch.call_args.kwargs["fetch_full_text"])
                self.assertTrue(fetch.call_args.kwargs["exhaustive_scan"])
                self.assertIsNone(fetch.call_args.kwargs["max_articles_per_source"])
                with get_connection(database) as connection:
                    self.assertEqual(
                        connection.execute("SELECT COUNT(*) FROM sources").fetchone()[
                            0
                        ],
                        30,
                    )
                    connection.execute("UPDATE sources SET active_status=0")
                    connection.commit()
                collect_publications(database)
                with get_connection(database) as connection:
                    self.assertEqual(
                        connection.execute(
                            "SELECT COUNT(*) FROM sources WHERE active_status=1"
                        ).fetchone()[0],
                        0,
                    )

    def test_only_recent_fulltext_three_publisher_coverage_qualifies(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "collection.db"
            initialize_database(database)
            with closing(get_connection(database)) as connection:
                for i in range(3):
                    upsert_source(
                        connection,
                        dict(
                            source_name=f"Publication {i}",
                            website_url=f"https://pub{i}.example",
                            category="Marketing",
                            country="Global",
                            credibility_tier=1,
                            active_status=1,
                        ),
                    )
                connection.commit()

            def fetch(**kwargs):
                with closing(get_connection(database)) as connection:
                    for source in connection.execute(
                        "SELECT * FROM sources"
                    ).fetchall():
                        upsert_article(
                            connection,
                            dict(
                                source_id=source["id"],
                                source_name=source["source_name"],
                                source_url=source["website_url"],
                                article_url=source["website_url"]
                                + "/news/nike-launches-olympic-advertising-campaign",
                                headline="Nike launches Olympic advertising campaign",
                                published_date=datetime.now(UTC).isoformat(),
                                article_text="Nike launches Olympic advertising campaign. "
                                * 20,
                                fetch_run_id=kwargs["run_id"],
                            ),
                        )
                    connection.commit()
                return FetchSummary(kwargs["run_id"], 3, 3, 3, 0, 0, [])

            with patch(
                "src.services.publication_collection.fetch_articles", side_effect=fetch
            ):
                result = collect_publications(database)
            self.assertEqual(len(result["topics"]), 1)
            self.assertEqual(result["articles_saved"], 3)
            self.assertEqual(len(result["topics"][0]["sources"]), 3)
            self.assertTrue(
                all(
                    s["approved"] and s["active_status"] == 1
                    for s in result["topics"][0]["sources"]
                )
            )
            with closing(get_connection(database)) as connection:
                connection.execute(
                    "UPDATE articles SET published_date=?",
                    ((datetime.now(UTC) - timedelta(days=10)).isoformat(),),
                )
                connection.commit()
            with patch(
                "src.services.publication_collection.fetch_articles",
                return_value=FetchSummary("none", 3, 0, 0, 0, 0, []),
            ):
                result = collect_publications(database, run_id=result["run_id"])
            self.assertEqual(result["topics"], [])
