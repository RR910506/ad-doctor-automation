from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from src.article_fetcher import FetchSummary
from src.services.publication_collection import collect_publications
from src.db import get_connection


class PublicationCollectionTests(TestCase):
    def test_import_full_text_and_empty_result_are_honest(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / 'collection.db'
            with patch('src.services.publication_collection.fetch_articles', return_value=FetchSummary('run', 30, 0, 0, 0, 30, ['Sources unavailable'])) as fetch:
                result = collect_publications(database)
                self.assertEqual(result['topics'], [])
                self.assertEqual(result['coverage_status'], 'bounded_scan')
                self.assertTrue(fetch.call_args.kwargs['fetch_full_text'])
                self.assertTrue(fetch.call_args.kwargs['exhaustive_scan'])
                self.assertIsNone(fetch.call_args.kwargs['max_articles_per_source'])
                with get_connection(database) as connection:
                    self.assertEqual(connection.execute('SELECT COUNT(*) FROM sources').fetchone()[0], 30)
                    connection.execute('UPDATE sources SET active_status=0')
                    connection.commit()
                collect_publications(database)
                with get_connection(database) as connection:
                    self.assertEqual(connection.execute('SELECT COUNT(*) FROM sources WHERE active_status=1').fetchone()[0], 0)
