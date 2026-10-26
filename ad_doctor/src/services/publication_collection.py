"""Connect the approved publication list to full-text collection and qualification."""
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4
from src.db import initialize_database, get_connection, list_topics
from src.source_manager import import_sources_from_file
from src.article_fetcher import fetch_articles
from src.topic_extractor import extract_topics
from src.qualification_engine import qualify_topics, get_exact_topic_scoring_inputs, count_unique_exact_sources

SOURCE_FILE = Path(__file__).resolve().parents[2] / 'data/input/approved_publications_2026-10-26.csv'


def collect_publications(database_path):
    database_path = Path(database_path)
    initialize_database(database_path)
    with closing(get_connection(database_path)) as connection:
        empty = connection.execute('SELECT COUNT(*) FROM sources').fetchone()[0] == 0
    if empty:
        with SOURCE_FILE.open('rb') as source:
            import_sources_from_file(source, filename=SOURCE_FILE.name, database_path=database_path)
    run_id = 'collection_' + uuid4().hex
    summary = fetch_articles(database_path=database_path, run_id=run_id,
                             fetch_full_text=True, exhaustive_scan=True,
                             max_articles_per_source=None, source_time_budget_seconds=90,
                             source_parallelism=4)
    extract_topics(database_path=database_path, run_id=run_id, article_fetch_run_id=run_id)
    qualify_topics(database_path=database_path, run_id=run_id)
    candidates = []
    with closing(get_connection(database_path)) as connection:
        for topic in list_topics(connection, limit=None):
            if topic['status'] != 'qualified':
                continue
            articles = get_exact_topic_scoring_inputs(connection, topic)['coverages']
            # Research only current-run, active-publication full text, one article per publisher.
            selected = {}
            for article in articles:
                active = connection.execute('SELECT 1 FROM articles a JOIN sources s ON s.id=a.source_id WHERE a.id=? AND a.fetch_run_id=? AND s.active_status=1', (article['article_id'], run_id)).fetchone()
                if active and len(str(article.get('article_text') or '').strip()) >= 300:
                    network = str(article.get('publisher_network') or article['source_name']).lower()
                    selected.setdefault(network, article)
            articles = list(selected.values())[:6]
            if count_unique_exact_sources(articles) < 3:
                continue
            material = '\n\n'.join(f"Publication: {a['source_name']}\nURL: {a['article_url']}\nHeadline: {a['headline']}\nExcerpt: {a['article_text'][:5000]}" for a in articles)
            candidates.append({'topic': topic['exact_topic'], 'material': material,
                               'publications': len(articles)})
    return {'run_id': run_id, 'collection': asdict(summary), 'topics': candidates,
            'coverage_status': 'bounded_scan',
            'limitations': '90 seconds per source. Discovery can miss pages; this is not exhaustive coverage. Empty results do not prove that no articles exist.'}
