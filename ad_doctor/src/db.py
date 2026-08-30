from __future__ import annotations

import sqlite3
import threading
import time
import os
from contextlib import closing
from pathlib import Path
from typing import Any

from src.config import DATABASE_PATH, SCHEMA_PATH


_INITIALIZATION_LOCK = threading.Lock()
_INITIALIZED_DATABASES: set[tuple[str, int]] = set()
DEFAULT_FETCH_SOURCE_TIME_BUDGET_SECONDS = 90


def get_connection(database_path: Path = DATABASE_PATH) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path, timeout=60)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 60000;")
    connection.execute("PRAGMA foreign_keys = ON;")
    connection.execute("PRAGMA synchronous = NORMAL;")
    return connection


def initialize_database(
    database_path: Path = DATABASE_PATH,
    schema_path: Path = SCHEMA_PATH,
) -> None:
    database_path = Path(database_path)
    schema_path = Path(schema_path)
    initialization_key = (str(database_path.resolve()), schema_path.stat().st_mtime_ns)
    if initialization_key in _INITIALIZED_DATABASES:
        return

    schema = schema_path.read_text(encoding="utf-8")
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with _INITIALIZATION_LOCK:
        if initialization_key in _INITIALIZED_DATABASES:
            return
        retry_locked_database(lambda: _initialize_database_once(database_path, schema))
        _INITIALIZED_DATABASES.add(initialization_key)


def _initialize_database_once(database_path: Path, schema: str) -> None:
    with closing(get_connection(database_path)) as connection:
        connection.execute("PRAGMA journal_mode = WAL;")
        connection.executescript(schema)
        run_migrations(connection)
        clear_generated_data_for_google_store_once(connection)
        connection.commit()


def retry_locked_database(operation, attempts: int = 5, delay_seconds: float = 0.75):
    last_error = None
    for attempt in range(attempts):
        try:
            return operation()
        except sqlite3.OperationalError as exc:
            last_error = exc
            if "database is locked" not in str(exc).lower() or attempt == attempts - 1:
                raise
            time.sleep(delay_seconds * (attempt + 1))
    if last_error:
        raise last_error
    return None


def run_migrations(connection: sqlite3.Connection) -> None:
    source_columns = {row["name"] for row in connection.execute("PRAGMA table_info(sources)").fetchall()}
    source_additions = {
        "domain": "TEXT", "publisher_network": "TEXT", "crawl_frequency_minutes": "INTEGER NOT NULL DEFAULT 60",
        "crawl_priority": "INTEGER NOT NULL DEFAULT 100", "browser_policy": "TEXT NOT NULL DEFAULT 'fallback'",
        "last_crawl": "TEXT", "last_success": "TEXT", "failure_count": "INTEGER NOT NULL DEFAULT 0",
    }
    for column, definition in source_additions.items():
        if column not in source_columns:
            connection.execute(f"ALTER TABLE sources ADD COLUMN {column} {definition}")
    article_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(articles)").fetchall()
    }
    if "fetch_run_id" not in article_columns:
        connection.execute("ALTER TABLE articles ADD COLUMN fetch_run_id TEXT")
    for column, definition in {"canonical_url": "TEXT", "content_hash": "TEXT", "extraction_method": "TEXT"}.items():
        if column not in article_columns:
            connection.execute(f"ALTER TABLE articles ADD COLUMN {column} {definition}")
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS browser_crawl_memory(id INTEGER PRIMARY KEY AUTOINCREMENT,source_id INTEGER NOT NULL,url TEXT NOT NULL,canonical_url TEXT NOT NULL UNIQUE,first_seen TEXT NOT NULL,last_seen TEXT NOT NULL,last_fetched TEXT,content_hash TEXT,http_status INTEGER,extraction_method TEXT,extraction_status TEXT NOT NULL,published_at TEXT,author TEXT,publisher_network TEXT,FOREIGN KEY(source_id) REFERENCES sources(id));
        CREATE INDEX IF NOT EXISTS idx_browser_crawl_source ON browser_crawl_memory(source_id,last_fetched);
        CREATE INDEX IF NOT EXISTS idx_browser_crawl_hash ON browser_crawl_memory(content_hash);
        CREATE TABLE IF NOT EXISTS browser_worker_runs(id INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT NOT NULL UNIQUE,status TEXT NOT NULL,started_at TEXT NOT NULL,finished_at TEXT,sources_monitored INTEGER NOT NULL DEFAULT 0,sources_checked INTEGER NOT NULL DEFAULT 0,urls_discovered INTEGER NOT NULL DEFAULT 0,articles_fetched INTEGER NOT NULL DEFAULT 0,http_extractions INTEGER NOT NULL DEFAULT 0,browser_fallbacks INTEGER NOT NULL DEFAULT 0,unchanged_skipped INTEGER NOT NULL DEFAULT 0,extraction_failures INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS research_documents(id INTEGER PRIMARY KEY AUTOINCREMENT,case_id TEXT NOT NULL,source_url TEXT NOT NULL,canonical_url TEXT NOT NULL,page_title TEXT NOT NULL,publication TEXT NOT NULL,publisher_network TEXT NOT NULL,retrieved_at TEXT NOT NULL,normalized_text TEXT NOT NULL,content_hash TEXT NOT NULL,extraction_method TEXT NOT NULL,provenance TEXT NOT NULL,UNIQUE(case_id,canonical_url,content_hash),FOREIGN KEY(case_id) REFERENCES cases(case_id) ON DELETE CASCADE);
    """)
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_articles_fetch_run_id ON articles (fetch_run_id)"
    )
    connection.execute("UPDATE research_provider_settings SET provider_key='browser',model='approved-source-browser',endpoint='',updated_at=CURRENT_TIMESTAMP WHERE provider_key='gemini' AND model='gemini-2.5-flash'")
    connection.execute("UPDATE content_writer_settings SET model='gemini-3.7-flash',updated_at=CURRENT_TIMESTAMP WHERE provider_key='gemini' AND model='gemini-2.5-flash'")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS automation_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            enabled INTEGER NOT NULL DEFAULT 1,
            schedule_time TEXT NOT NULL DEFAULT '09:00',
            schedule_times TEXT NOT NULL DEFAULT '["09:00"]',
            timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata',
            task_name TEXT NOT NULL DEFAULT 'AdDoctorDailyScrape',
            notify_on_success INTEGER NOT NULL DEFAULT 1,
            notify_on_failure INTEGER NOT NULL DEFAULT 1,
            fetch_source_time_budget_seconds INTEGER NOT NULL DEFAULT 90,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    automation_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(automation_settings)").fetchall()
    }
    if "schedule_times" not in automation_columns:
        connection.execute("ALTER TABLE automation_settings ADD COLUMN schedule_times TEXT NOT NULL DEFAULT '[\"09:00\"]'")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS app_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            display_name TEXT,
            notifications_enabled INTEGER NOT NULL DEFAULT 1,
            last_login_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (email)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS notification_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT,
            recipient_email TEXT NOT NULL,
            subject TEXT NOT NULL,
            status TEXT NOT NULL,
            error_message TEXT,
            sent_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_app_users_notifications_enabled ON app_users (notifications_enabled)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_notification_events_run_id ON notification_events (run_id)"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS email_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            smtp_host TEXT,
            smtp_port INTEGER NOT NULL DEFAULT 587,
            smtp_username TEXT,
            smtp_password TEXT,
            smtp_from TEXT,
            use_tls INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS research_provider_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            provider_key TEXT NOT NULL DEFAULT 'browser',
            api_key TEXT,
            model TEXT NOT NULL DEFAULT 'approved-source-browser',
            endpoint TEXT NOT NULL DEFAULT 'https://generativelanguage.googleapis.com',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS content_writer_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            provider_key TEXT NOT NULL DEFAULT 'gemini',
            enabled INTEGER NOT NULL DEFAULT 1,
            api_key TEXT,
            model TEXT NOT NULL DEFAULT 'gemini-3.7-flash',
            endpoint TEXT NOT NULL DEFAULT 'https://generativelanguage.googleapis.com',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS content_framework_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            framework_name TEXT NOT NULL DEFAULT 'The Ad Doctor Diagnostic Carousel',
            brand_worldview TEXT NOT NULL,
            qualification_rule TEXT NOT NULL,
            content_pillars TEXT NOT NULL,
            carousel_structure TEXT NOT NULL,
            tone_rules TEXT NOT NULL,
            psychology_lenses TEXT NOT NULL,
            consumer_lenses TEXT NOT NULL,
            culture_lenses TEXT NOT NULL,
            caption_rules TEXT NOT NULL,
            fact_check_rules TEXT NOT NULL,
            designer_rules TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS automation_run_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            schedule_key TEXT NOT NULL,
            scheduled_date TEXT NOT NULL,
            scheduled_time TEXT NOT NULL,
            timezone TEXT NOT NULL,
            run_id TEXT,
            status TEXT NOT NULL,
            error_message TEXT,
            current_phase TEXT,
            current_source TEXT,
            total_sources INTEGER NOT NULL DEFAULT 0,
            completed_sources INTEGER NOT NULL DEFAULT 0,
            failed_sources INTEGER NOT NULL DEFAULT 0,
            articles_scanned INTEGER NOT NULL DEFAULT 0,
            qualified_topics INTEGER NOT NULL DEFAULT 0,
            social_drafts_created INTEGER NOT NULL DEFAULT 0,
            blog_drafts_created INTEGER NOT NULL DEFAULT 0,
            research_calls INTEGER NOT NULL DEFAULT 0,
            tracked_tokens INTEGER NOT NULL DEFAULT 0,
            estimated_credits INTEGER NOT NULL DEFAULT 0,
            heartbeat_at TEXT,
            worker_id TEXT,
            started_at TEXT,
            finished_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (schedule_key)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS automation_worker_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            status TEXT NOT NULL DEFAULT 'starting',
            message TEXT,
            worker_id TEXT,
            last_heartbeat_at TEXT,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    ensure_columns(
        connection,
        "automation_run_history",
        {
            "current_phase": "TEXT",
            "current_source": "TEXT",
            "total_sources": "INTEGER NOT NULL DEFAULT 0",
            "completed_sources": "INTEGER NOT NULL DEFAULT 0",
            "failed_sources": "INTEGER NOT NULL DEFAULT 0",
            "articles_scanned": "INTEGER NOT NULL DEFAULT 0",
            "qualified_topics": "INTEGER NOT NULL DEFAULT 0",
            "social_drafts_created": "INTEGER NOT NULL DEFAULT 0",
            "blog_drafts_created": "INTEGER NOT NULL DEFAULT 0",
            "research_calls": "INTEGER NOT NULL DEFAULT 0",
            "tracked_tokens": "INTEGER NOT NULL DEFAULT 0",
            "estimated_credits": "INTEGER NOT NULL DEFAULT 0",
            "heartbeat_at": "TEXT",
            "worker_id": "TEXT",
        },
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_automation_run_history_scheduled_at ON automation_run_history (scheduled_date, scheduled_time)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_automation_run_history_status ON automation_run_history (status)"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS system_flags (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    ensure_automation_settings(connection)
    migrate_fetch_source_budget_default(connection)


def ensure_columns(
    connection: sqlite3.Connection,
    table_name: str,
    columns: dict[str, str],
) -> None:
    existing_columns = {
        row["name"]
        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    for column_name, column_definition in columns.items():
        if column_name not in existing_columns:
            connection.execute(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}"
            )


def clear_generated_data_for_google_store_once(connection: sqlite3.Connection) -> None:
    should_clear = (
        os.getenv("AD_DOCTOR_CLEAR_SQLITE_GENERATED_DATA_FOR_GOOGLE", "0")
        .strip()
        .lower()
        in {"1", "true", "yes", "on"}
    )
    if not should_clear:
        return

    flag_key = "google_draft_store_generated_data_reset_v1"
    existing = connection.execute(
        "SELECT value FROM system_flags WHERE key = ?",
        (flag_key,),
    ).fetchone()
    if existing:
        return

    connection.execute("PRAGMA foreign_keys = OFF")
    for table in (
        "approvals",
        "content_outputs",
        "dissection_outputs",
        "research_dossiers",
        "locked_topics",
        "topic_scores",
        "topic_sources",
        "topics",
        "articles",
    ):
        connection.execute(f"DELETE FROM {table}")
        connection.execute("DELETE FROM sqlite_sequence WHERE name = ?", (table,))
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(
        """
        INSERT INTO system_flags (key, value, updated_at)
        VALUES (?, 'complete', CURRENT_TIMESTAMP)
        """,
        (flag_key,),
    )


def ensure_automation_settings(connection: sqlite3.Connection) -> None:
    existing = connection.execute(
        "SELECT 1 FROM automation_settings WHERE id = 1"
    ).fetchone()
    if existing:
        return
    connection.execute(
        """
        INSERT INTO automation_settings (
            id,
            enabled,
            schedule_time,
            timezone,
            task_name,
            notify_on_success,
            notify_on_failure,
            fetch_source_time_budget_seconds,
            schedule_times
        )
        VALUES (1, 1, '09:00', 'Asia/Kolkata', 'AdDoctorDailyScrape', 1, 1, 90, '["09:00"]')
        """
    )


def migrate_fetch_source_budget_default(connection: sqlite3.Connection) -> None:
    flag_key = "fetch_source_budget_reduced_to_90_v1"
    existing = connection.execute(
        "SELECT value FROM system_flags WHERE key = ?",
        (flag_key,),
    ).fetchone()
    if existing:
        return
    connection.execute(
        """
        UPDATE automation_settings
        SET fetch_source_time_budget_seconds = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = 1
          AND fetch_source_time_budget_seconds >= 180
        """,
        (DEFAULT_FETCH_SOURCE_TIME_BUDGET_SECONDS,),
    )
    connection.execute(
        """
        INSERT INTO system_flags (key, value, updated_at)
        VALUES (?, 'complete', CURRENT_TIMESTAMP)
        """,
        (flag_key,),
    )


def upsert_source(connection: sqlite3.Connection, source: dict[str, Any]) -> str:
    existing = connection.execute(
        "SELECT id FROM sources WHERE website_url = ?",
        (source["website_url"],),
    ).fetchone()

    connection.execute(
        """
        INSERT INTO sources (
            source_name,
            website_url,
            rss_url,
            category,
            country,
            domain,
            publisher_network,
            credibility_tier,
            active_status,
            crawl_frequency_minutes,
            crawl_priority,
            browser_policy
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(website_url) DO UPDATE SET
            source_name = excluded.source_name,
            rss_url = excluded.rss_url,
            category = excluded.category,
            country = excluded.country,
            domain = excluded.domain,
            publisher_network = excluded.publisher_network,
            credibility_tier = excluded.credibility_tier,
            active_status = excluded.active_status,
            crawl_frequency_minutes = excluded.crawl_frequency_minutes,
            crawl_priority = excluded.crawl_priority,
            browser_policy = excluded.browser_policy,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            source["source_name"],
            source["website_url"],
            source.get("rss_url"),
            source.get("category"),
            source.get("country"),
            source.get("domain"),
            source.get("publisher_network") or ("Campaign" if str(source["source_name"]).strip().lower().startswith("campaign ") else source["source_name"]),
            source.get("credibility_tier", 1),
            source.get("active_status", 1),
            source.get("crawl_frequency_minutes", 60),
            source.get("crawl_priority", 100),
            source.get("browser_policy", "fallback"),
        ),
    )
    return "updated" if existing else "inserted"


def update_source_rss_url(connection: sqlite3.Connection, source_id: int, rss_url: str) -> None:
    connection.execute(
        """
        UPDATE sources
        SET rss_url = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND (rss_url IS NULL OR rss_url = '')
        """,
        (rss_url, source_id),
    )


def list_sources(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT
            id,
            source_name,
            website_url,
            rss_url,
            category,
            country,
            domain,
            publisher_network,
            credibility_tier,
            active_status,
            crawl_frequency_minutes,
            crawl_priority,
            browser_policy,
            last_crawl,
            last_success,
            failure_count,
            created_at,
            updated_at
        FROM sources
        ORDER BY active_status DESC, credibility_tier DESC, source_name ASC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def count_active_sources(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        "SELECT COUNT(*) AS total FROM sources WHERE active_status = 1"
    ).fetchone()
    return int(row["total"])


def count_active_publisher_networks(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        "SELECT COUNT(DISTINCT lower(COALESCE(NULLIF(publisher_network,''),source_name))) AS total FROM sources WHERE active_status=1"
    ).fetchone()
    return int(row["total"])


def list_active_sources(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT
            id,
            source_name,
            website_url,
            rss_url,
            category,
            country,
            domain,
            publisher_network,
            credibility_tier,
            active_status,
            crawl_frequency_minutes,
            crawl_priority,
            browser_policy,
            last_crawl,
            last_success,
            failure_count
        FROM sources
        WHERE active_status = 1
        ORDER BY crawl_priority ASC, credibility_tier DESC, source_name ASC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def upsert_article(connection: sqlite3.Connection, article: dict[str, Any]) -> str:
    existing = connection.execute(
        "SELECT id FROM articles WHERE article_url = ?",
        (article["article_url"],),
    ).fetchone()
    connection.execute(
        """
        INSERT INTO articles (
            source_id,
            source_name,
            source_url,
            article_url,
            headline,
            published_date,
            author,
            meta_description,
            article_text,
            fetch_run_id,
            canonical_url,
            content_hash,
            extraction_method,
            fetch_timestamp
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(article_url) DO UPDATE SET
            source_id = excluded.source_id,
            source_name = excluded.source_name,
            source_url = excluded.source_url,
            headline = excluded.headline,
            published_date = excluded.published_date,
            author = excluded.author,
            meta_description = excluded.meta_description,
            article_text = excluded.article_text,
            fetch_run_id = excluded.fetch_run_id,
            canonical_url = excluded.canonical_url,
            content_hash = excluded.content_hash,
            extraction_method = excluded.extraction_method,
            fetch_timestamp = CURRENT_TIMESTAMP
        """,
        (
            article["source_id"],
            article["source_name"],
            article["source_url"],
            article["article_url"],
            article.get("headline"),
            article.get("published_date"),
            article.get("author"),
            article.get("meta_description"),
            article.get("article_text"),
            article.get("fetch_run_id"),
            article.get("canonical_url") or article.get("article_url"),
            article.get("content_hash"),
            article.get("extraction_method") or "http",
        ),
    )
    return "updated" if existing else "inserted"


def delete_demo_articles(connection: sqlite3.Connection) -> int:
    cursor = connection.execute(
        """
        DELETE FROM articles
        WHERE article_url LIKE '%/demo/%'
            OR fetch_run_id LIKE 'demo_%'
        """
    )
    return int(cursor.rowcount or 0)


def get_article_id_by_url(connection: sqlite3.Connection, article_url: str) -> int | None:
    row = connection.execute(
        "SELECT id FROM articles WHERE article_url = ?",
        (article_url,),
    ).fetchone()
    return int(row["id"]) if row else None


def list_articles(
    connection: sqlite3.Connection,
    limit: int | None = 200,
    fetch_run_id: str | None = None,
) -> list[dict[str, Any]]:
    if fetch_run_id:
        limit_clause = "" if limit is None else "LIMIT ?"
        params: tuple[Any, ...] = (fetch_run_id,) if limit is None else (fetch_run_id, limit)
        rows = connection.execute(
            f"""
            SELECT
                id,
                source_id,
                source_name,
                source_url,
                article_url,
                headline,
                published_date,
                author,
                meta_description,
                article_text,
                fetch_run_id,
                fetch_timestamp
            FROM articles
            WHERE fetch_run_id = ?
            ORDER BY fetch_timestamp DESC, id DESC
            {limit_clause}
            """,
            params,
        ).fetchall()
    else:
        limit_clause = "" if limit is None else "LIMIT ?"
        params = () if limit is None else (limit,)
        rows = connection.execute(
            f"""
            SELECT
                id,
                source_id,
                source_name,
                source_url,
                article_url,
                headline,
                published_date,
                author,
                meta_description,
                article_text,
                fetch_run_id,
                fetch_timestamp
            FROM articles
            ORDER BY fetch_timestamp DESC, id DESC
            {limit_clause}
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def count_articles(connection: sqlite3.Connection) -> int:
    row = connection.execute("SELECT COUNT(*) AS total FROM articles").fetchone()
    return int(row["total"])


def count_topics(connection: sqlite3.Connection, status: str | None = None) -> int:
    if status:
        row = connection.execute(
            "SELECT COUNT(*) AS total FROM topics WHERE status = ?",
            (status,),
        ).fetchone()
    else:
        row = connection.execute("SELECT COUNT(*) AS total FROM topics").fetchone()
    return int(row["total"])


def list_article_source_counts(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT
            source_name,
            COUNT(*) AS article_count,
            MAX(fetch_timestamp) AS latest_fetch_timestamp
        FROM articles
        GROUP BY source_name
        ORDER BY article_count DESC, source_name ASC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def list_articles_by_source(
    connection: sqlite3.Connection,
    source_name: str | None = None,
    limit: int | None = 500,
) -> list[dict[str, Any]]:
    if source_name:
        limit_clause = "" if limit is None else "LIMIT ?"
        params: tuple[Any, ...] = (source_name,) if limit is None else (source_name, limit)
        rows = connection.execute(
            f"""
            SELECT
                id,
                source_id,
                source_name,
                source_url,
                article_url,
                headline,
                published_date,
                author,
                meta_description,
                article_text,
                fetch_run_id,
                fetch_timestamp
            FROM articles
            WHERE source_name = ?
            ORDER BY fetch_timestamp DESC, id DESC
            {limit_clause}
            """,
            params,
        ).fetchall()
    else:
        rows = list_articles(connection, limit=limit)
    return [dict(row) for row in rows]


def log_run_event(
    connection: sqlite3.Connection,
    run_id: str,
    phase: str,
    event_type: str,
    message: str,
    metadata_json: str | None = None,
    auto_commit: bool = False,
) -> None:
    connection.execute(
        """
        INSERT INTO run_logs (run_id, phase, event_type, message, metadata_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        (run_id, phase, event_type, message, metadata_json),
    )
    if auto_commit:
        connection.commit()


def list_run_logs(
    connection: sqlite3.Connection,
    phase: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    if phase:
        rows = connection.execute(
            """
            SELECT id, run_id, phase, event_type, message, metadata_json, created_at
            FROM run_logs
            WHERE phase = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (phase, limit),
        ).fetchall()
    else:
        rows = connection.execute(
            """
            SELECT id, run_id, phase, event_type, message, metadata_json, created_at
            FROM run_logs
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_latest_article_fetch_run_id(connection: sqlite3.Connection) -> str | None:
    row = connection.execute(
        """
        SELECT run_id
        FROM run_logs
        WHERE phase = 'phase_3_article_fetch' AND event_type = 'complete'
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    return str(row["run_id"]) if row else None


def upsert_topic(
    connection: sqlite3.Connection,
    exact_topic: str,
    normalized_topic: str,
    representative_headline: str | None = None,
) -> int:
    connection.execute(
        """
        INSERT INTO topics (exact_topic, normalized_topic, representative_headline)
        VALUES (?, ?, ?)
        ON CONFLICT(normalized_topic) DO UPDATE SET
            exact_topic = topics.exact_topic,
            representative_headline = COALESCE(topics.representative_headline, excluded.representative_headline)
        """,
        (exact_topic, normalized_topic, representative_headline or exact_topic),
    )
    row = connection.execute(
        "SELECT id FROM topics WHERE normalized_topic = ?",
        (normalized_topic,),
    ).fetchone()
    return int(row["id"])


def link_topic_source(
    connection: sqlite3.Connection,
    topic_id: int,
    source_id: int,
    article_id: int,
    coverage_url: str,
) -> None:
    connection.execute(
        """
        INSERT OR IGNORE INTO topic_sources (topic_id, source_id, article_id, coverage_url)
        VALUES (?, ?, ?, ?)
        """,
        (topic_id, source_id, article_id, coverage_url),
    )


def list_topics(connection: sqlite3.Connection, limit: int | None = 200) -> list[dict[str, Any]]:
    limit_clause = "" if limit is None else "LIMIT ?"
    params: tuple[Any, ...] = () if limit is None else (limit,)
    rows = connection.execute(
        f"""
        SELECT
            t.id,
            t.exact_topic,
            t.normalized_topic,
            t.representative_headline,
            t.status,
            t.created_at,
            COUNT(DISTINCT lower(COALESCE(NULLIF(s.publisher_network,''),s.source_name))) AS source_coverage_count,
            COUNT(DISTINCT ts.article_id) AS article_count
        FROM topics t
        LEFT JOIN topic_sources ts ON ts.topic_id = t.id
        LEFT JOIN sources s ON s.id = ts.source_id
        GROUP BY t.id
        ORDER BY source_coverage_count DESC, article_count DESC, t.created_at DESC
        {limit_clause}
        """,
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def list_topic_sources(connection: sqlite3.Connection, topic_id: int) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT
            ts.id,
            ts.topic_id,
            ts.source_id,
            s.source_name,
            ts.article_id,
            a.headline,
            ts.coverage_url,
            ts.created_at
        FROM topic_sources ts
        JOIN sources s ON s.id = ts.source_id
        JOIN articles a ON a.id = ts.article_id
        WHERE ts.topic_id = ?
        ORDER BY s.source_name ASC, a.fetch_timestamp DESC
        """,
        (topic_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def upsert_topic_score(connection: sqlite3.Connection, score: dict[str, Any]) -> None:
    connection.execute(
        """
        INSERT INTO topic_scores (
            topic_id,
            active_source_count,
            unique_source_coverage_count,
            minimum_required_sources,
            coverage_percentage,
            credibility_score,
            freshness_score,
            data_richness_score,
            official_reference_score,
            priority_boost_applied,
            total_score
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(topic_id) DO UPDATE SET
            active_source_count = excluded.active_source_count,
            unique_source_coverage_count = excluded.unique_source_coverage_count,
            minimum_required_sources = excluded.minimum_required_sources,
            coverage_percentage = excluded.coverage_percentage,
            credibility_score = excluded.credibility_score,
            freshness_score = excluded.freshness_score,
            data_richness_score = excluded.data_richness_score,
            official_reference_score = excluded.official_reference_score,
            priority_boost_applied = excluded.priority_boost_applied,
            total_score = excluded.total_score,
            created_at = CURRENT_TIMESTAMP
        """,
        (
            score["topic_id"],
            score["active_source_count"],
            score["unique_source_coverage_count"],
            score["minimum_required_sources"],
            score["coverage_percentage"],
            score.get("credibility_score", 0),
            score.get("freshness_score", 0),
            score.get("data_richness_score", 0),
            score.get("official_reference_score", 0),
            score.get("priority_boost_applied", 0),
            score.get("total_score", 0),
        ),
    )


def update_topic_status(connection: sqlite3.Connection, topic_id: int, status: str) -> None:
    connection.execute(
        "UPDATE topics SET status = ? WHERE id = ?",
        (status, topic_id),
    )


def get_topic_scoring_inputs(connection: sqlite3.Connection, topic_id: int) -> dict[str, Any]:
    rows = connection.execute(
        """
        SELECT
            ts.source_id,
            s.credibility_tier,
            a.published_date,
            a.fetch_timestamp,
            LENGTH(COALESCE(a.article_text, '')) AS article_text_length,
            a.article_url
        FROM topic_sources ts
        JOIN sources s ON s.id = ts.source_id
        JOIN articles a ON a.id = ts.article_id
        WHERE ts.topic_id = ?
        """,
        (topic_id,),
    ).fetchall()
    return {"coverages": [dict(row) for row in rows]}


def list_topic_scores(connection: sqlite3.Connection, qualified_only: bool = False) -> list[dict[str, Any]]:
    status_clause = "WHERE t.status = 'qualified'" if qualified_only else ""
    rows = connection.execute(
        f"""
        SELECT
            t.id AS topic_id,
            t.exact_topic,
            t.representative_headline,
            t.status,
            ts.active_source_count,
            ts.unique_source_coverage_count,
            ts.minimum_required_sources,
            ts.coverage_percentage,
            ts.credibility_score,
            ts.freshness_score,
            ts.data_richness_score,
            ts.official_reference_score,
            ts.priority_boost_applied,
            ts.total_score,
            ts.created_at AS scored_at
        FROM topics t
        JOIN topic_scores ts ON ts.topic_id = t.id
        {status_clause}
        ORDER BY
            t.status = 'qualified' DESC,
            ts.coverage_percentage DESC,
            ts.credibility_score DESC,
            ts.freshness_score DESC,
            ts.data_richness_score DESC,
            ts.official_reference_score DESC,
            ts.total_score DESC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def lock_topic(connection: sqlite3.Connection, topic_id: int, run_id: str, locked_topic: str) -> None:
    connection.execute(
        """
        INSERT OR IGNORE INTO locked_topics (topic_id, run_id, locked_topic)
        VALUES (?, ?, ?)
        """,
        (topic_id, run_id, locked_topic),
    )


def get_latest_locked_run_id(connection: sqlite3.Connection) -> str | None:
    row = connection.execute(
        """
        SELECT run_id
        FROM locked_topics
        ORDER BY locked_at DESC, id DESC
        LIMIT 1
        """
    ).fetchone()
    return str(row["run_id"]) if row else None


def list_locked_topics(connection: sqlite3.Connection, run_id: str | None = None) -> list[dict[str, Any]]:
    if run_id:
        rows = connection.execute(
            """
            SELECT
                lt.id,
                lt.topic_id,
                lt.run_id,
                lt.locked_topic,
                lt.locked_at,
                t.status,
                ts.coverage_percentage,
                ts.total_score
            FROM locked_topics lt
            JOIN topics t ON t.id = lt.topic_id
            LEFT JOIN topic_scores ts ON ts.topic_id = lt.topic_id
            WHERE lt.run_id = ?
            ORDER BY lt.locked_at DESC, lt.id DESC
            """,
            (run_id,),
        ).fetchall()
    else:
        rows = connection.execute(
            """
            SELECT
                lt.id,
                lt.topic_id,
                lt.run_id,
                lt.locked_topic,
                lt.locked_at,
                t.status,
                ts.coverage_percentage,
                ts.total_score
            FROM locked_topics lt
            JOIN topics t ON t.id = lt.topic_id
            LEFT JOIN topic_scores ts ON ts.topic_id = lt.topic_id
            ORDER BY lt.locked_at DESC, lt.id DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def get_locked_topic_context(connection: sqlite3.Connection, locked_topic_id: int) -> dict[str, Any]:
    locked_topic = connection.execute(
        """
        SELECT
            lt.id,
            lt.topic_id,
            lt.run_id,
            lt.locked_topic,
            lt.locked_at,
            t.exact_topic,
            t.representative_headline
        FROM locked_topics lt
        JOIN topics t ON t.id = lt.topic_id
        WHERE lt.id = ?
        """,
        (locked_topic_id,),
    ).fetchone()
    if not locked_topic:
        raise ValueError(f"Locked topic not found: {locked_topic_id}")

    articles = connection.execute(
        """
        SELECT
            a.id,
            a.source_name,
            a.source_url,
            a.article_url,
            a.headline,
            a.published_date,
            a.author,
            a.meta_description,
            a.article_text,
            a.fetch_timestamp
        FROM topic_sources ts
        JOIN articles a ON a.id = ts.article_id
        WHERE ts.topic_id = ?
        ORDER BY a.published_date DESC, a.fetch_timestamp DESC
        """,
        (locked_topic["topic_id"],),
    ).fetchall()
    return {
        "locked_topic": dict(locked_topic),
        "articles": [dict(row) for row in articles],
    }


def upsert_research_dossier(
    connection: sqlite3.Connection,
    locked_topic_id: int,
    dossier_json: str,
    source_urls_json: str,
) -> None:
    connection.execute(
        """
        INSERT INTO research_dossiers (locked_topic_id, dossier_json, source_urls_json)
        VALUES (?, ?, ?)
        ON CONFLICT(locked_topic_id) DO UPDATE SET
            dossier_json = excluded.dossier_json,
            source_urls_json = excluded.source_urls_json,
            updated_at = CURRENT_TIMESTAMP
        """,
        (locked_topic_id, dossier_json, source_urls_json),
    )


def list_research_dossiers(
    connection: sqlite3.Connection,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    run_filter = "WHERE lt.run_id = ?" if run_id else ""
    params: tuple[Any, ...] = (run_id,) if run_id else ()
    rows = connection.execute(
        f"""
        SELECT
            rd.id,
            rd.locked_topic_id,
            lt.locked_topic,
            lt.run_id,
            rd.dossier_json,
            rd.source_urls_json,
            rd.created_at,
            rd.updated_at
        FROM research_dossiers rd
        JOIN locked_topics lt ON lt.id = rd.locked_topic_id
        {run_filter}
        ORDER BY rd.updated_at DESC, rd.id DESC
        """,
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def insert_dissection_output(
    connection: sqlite3.Connection,
    locked_topic_id: int,
    template_version: str,
    output_markdown: str,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO dissection_outputs (locked_topic_id, template_version, output_markdown)
        VALUES (?, ?, ?)
        """,
        (locked_topic_id, template_version, output_markdown),
    )
    return int(cursor.lastrowid)


def list_dissection_outputs(
    connection: sqlite3.Connection,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    run_filter = "WHERE lt.run_id = ?" if run_id else ""
    params: tuple[Any, ...] = (run_id,) if run_id else ()
    rows = connection.execute(
        f"""
        SELECT
            do.id,
            do.locked_topic_id,
            lt.locked_topic,
            lt.run_id,
            do.template_version,
            do.output_markdown,
            do.created_at,
            do.updated_at
        FROM dissection_outputs do
        JOIN locked_topics lt ON lt.id = do.locked_topic_id
        {run_filter}
        ORDER BY do.created_at DESC, do.id DESC
        """,
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def insert_content_output(
    connection: sqlite3.Connection,
    locked_topic_id: int,
    content_type: str,
    template_version: str,
    draft_markdown: str,
    approval_status: str = "pending",
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO content_outputs (
            locked_topic_id,
            content_type,
            template_version,
            draft_markdown,
            approval_status
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (locked_topic_id, content_type, template_version, draft_markdown, approval_status),
    )
    return int(cursor.lastrowid)


def list_content_outputs(
    connection: sqlite3.Connection,
    content_type: str | None = None,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    filters = []
    params: list[Any] = []
    if content_type:
        filters.append("co.content_type = ?")
        params.append(content_type)
    if run_id:
        filters.append("lt.run_id = ?")
        params.append(run_id)
    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
    rows = connection.execute(
        f"""
        SELECT
            co.id,
            co.locked_topic_id,
            lt.locked_topic,
            lt.run_id,
            co.content_type,
            co.template_version,
            co.draft_markdown,
            co.approval_status,
            co.created_at,
            co.updated_at
        FROM content_outputs co
        JOIN locked_topics lt ON lt.id = co.locked_topic_id
        {where_clause}
        ORDER BY co.created_at DESC, co.id DESC
        """,
        tuple(params),
    ).fetchall()
    return [dict(row) for row in rows]


def update_content_approval_status(
    connection: sqlite3.Connection,
    content_output_id: int,
    status: str,
) -> None:
    connection.execute(
        """
        UPDATE content_outputs
        SET approval_status = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (status, content_output_id),
    )


def insert_approval(
    connection: sqlite3.Connection,
    content_output_id: int,
    approval_type: str,
    status: str,
    reviewer_notes: str | None = None,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO approvals (content_output_id, approval_type, status, reviewer_notes, approved_at)
        VALUES (?, ?, ?, ?, CASE WHEN ? = 'approved' THEN CURRENT_TIMESTAMP ELSE NULL END)
        """,
        (content_output_id, approval_type, status, reviewer_notes, status),
    )
    return int(cursor.lastrowid)


def list_approval_queue(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        WITH latest_content AS (
            SELECT
                locked_topic_id,
                content_type,
                MAX(created_at) AS latest_created_at
            FROM content_outputs
            WHERE approval_status != 'rejected'
            GROUP BY locked_topic_id, content_type
        )
        SELECT
            co.id AS content_output_id,
            lt.locked_topic,
            co.content_type,
            co.template_version,
            co.draft_markdown,
            co.approval_status,
            co.created_at,
            co.updated_at
        FROM content_outputs co
        JOIN latest_content latest
            ON latest.locked_topic_id = co.locked_topic_id
            AND latest.content_type = co.content_type
            AND latest.latest_created_at = co.created_at
        JOIN locked_topics lt ON lt.id = co.locked_topic_id
        WHERE co.approval_status != 'rejected'
        ORDER BY
            co.approval_status = 'pending' DESC,
            co.updated_at DESC,
            co.id DESC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def list_approved_content_outputs(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT
            co.id,
            co.locked_topic_id,
            lt.locked_topic,
            lt.run_id,
            co.content_type,
            co.template_version,
            co.draft_markdown,
            co.approval_status,
            co.created_at,
            co.updated_at
        FROM content_outputs co
        JOIN locked_topics lt ON lt.id = co.locked_topic_id
        WHERE co.approval_status = 'approved'
        ORDER BY co.updated_at DESC, co.id DESC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def clear_derived_pipeline_data(connection: sqlite3.Connection) -> None:
    for table in (
        "approvals",
        "content_outputs",
        "dissection_outputs",
        "research_dossiers",
        "locked_topics",
        "topic_scores",
        "topic_sources",
        "topics",
    ):
        connection.execute(f"DELETE FROM {table}")
