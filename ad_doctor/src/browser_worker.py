from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import time
from typing import Callable

from src.browser_research import ArticleRetrievalService, CrawlControls, CrawlMemoryRepository, DiscoveryService, RequestsHttpService
from src.config import DATABASE_PATH
from src.db import get_connection, initialize_database, list_active_sources, upsert_article


@dataclass(frozen=True, slots=True)
class BrowserCycleSummary:
    run_id: str
    sources_monitored: int
    sources_checked: int
    urls_discovered: int
    articles_fetched: int
    http_extractions: int
    browser_fallbacks: int
    unchanged_skipped: int
    extraction_failures: int


class SourceRegistryService:
    """SQLite Sources is authoritative; Google Sheets is an optional import path."""

    def __init__(self, database_path: Path = DATABASE_PATH) -> None:
        self.database_path = database_path

    def active_sources(self) -> list[dict[str, object]]:
        with closing(get_connection(self.database_path)) as connection:
            return list_active_sources(connection)

    def optional_google_sheet_sync(self, sheet_url: str | None) -> bool:
        if not str(sheet_url or "").strip():
            return False
        from src.source_manager import import_sources_from_google_sheet
        import_sources_from_google_sheet(str(sheet_url), database_path=self.database_path)
        return True


class BrowserWorker:
    def __init__(self, *, database_path: Path = DATABASE_PATH, controls: CrawlControls | None = None, http: object | None = None, discovery: DiscoveryService | None = None, retrieval: ArticleRetrievalService | None = None) -> None:
        self.database_path = Path(database_path)
        self.controls = controls or CrawlControls.from_environment()
        self.http = http or RequestsHttpService()
        self.discovery = discovery or DiscoveryService()
        self.retrieval = retrieval or ArticleRetrievalService(http=self.http, controls=self.controls)

    def run_once(self) -> BrowserCycleSummary:
        initialize_database(self.database_path)
        run_id = "browser_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        sources = SourceRegistryService(self.database_path).active_sources()
        checked = discovered = fetched = http_count = browser_count = unchanged = failures = 0
        with closing(get_connection(self.database_path)) as connection:
            connection.execute("INSERT INTO browser_worker_runs(run_id,status,started_at,sources_monitored) VALUES(?, 'running', CURRENT_TIMESTAMP, ?)", (run_id, len(sources)))
            connection.commit()
        for source in sources:
            if fetched + failures >= self.controls.max_pages_per_cycle:
                break
            if int(source.get("failure_count") or 0) >= self.controls.failure_circuit_breaker:
                continue
            if not source_is_due(source):
                continue
            checked += 1
            source_urls: list[str] = []
            try:
                discovery_url = str(source.get("rss_url") or source.get("website_url") or "")
                page = self.http.get(discovery_url, timeout_seconds=self.controls.http_timeout_seconds)
                lowered = page.html.lstrip().lower()
                if source.get("rss_url") or lowered.startswith("<?xml") and ("<rss" in lowered or "<feed" in lowered):
                    source_urls = self.discovery.discover_feed(page.html, source)
                elif "<urlset" in lowered or "<sitemapindex" in lowered:
                    source_urls = self.discovery.discover_sitemap(page.html, source)
                else:
                    source_urls = self.discovery.discover_category(page.html, page.url, source)
                source_urls = source_urls[: self.controls.max_pages_per_source_per_cycle]
                discovered += len(source_urls)
                source_failures = 0
                for url in source_urls:
                    if fetched + failures >= self.controls.max_pages_per_cycle:
                        break
                    try:
                        document = self.retrieval.retrieve(url, source)
                        with closing(get_connection(self.database_path)) as connection:
                            memory = CrawlMemoryRepository(connection)
                            if memory.unchanged(document):
                                unchanged += 1
                            else:
                                upsert_article(connection, {
                                    "source_id": source["id"], "source_name": source["source_name"], "source_url": source["website_url"],
                                    "article_url": document.canonical_url, "headline": document.page_title, "published_date": document.published_at,
                                    "author": document.author, "meta_description": None, "article_text": document.text, "fetch_run_id": run_id,
                                    "canonical_url": document.canonical_url, "content_hash": document.content_hash, "extraction_method": document.extraction_method,
                                })
                                fetched += 1
                                http_count += int(document.extraction_method == "http")
                                browser_count += int(document.extraction_method == "browser")
                            memory.record(int(source["id"]), document)
                            connection.commit()
                    except Exception:
                        failures += 1
                        source_failures += 1
                with closing(get_connection(self.database_path)) as connection:
                    connection.execute("UPDATE sources SET last_crawl=CURRENT_TIMESTAMP,last_success=CASE WHEN ?=0 THEN CURRENT_TIMESTAMP ELSE last_success END,failure_count=CASE WHEN ?=0 THEN 0 ELSE failure_count+? END WHERE id=?", (source_failures, source_failures, source_failures, source["id"]))
                    connection.commit()
            except Exception:
                failures += 1
                with closing(get_connection(self.database_path)) as connection:
                    connection.execute("UPDATE sources SET last_crawl=CURRENT_TIMESTAMP,failure_count=failure_count+1 WHERE id=?", (source["id"],))
                    connection.commit()
        summary = BrowserCycleSummary(run_id, len(sources), checked, discovered, fetched, http_count, browser_count, unchanged, failures)
        with closing(get_connection(self.database_path)) as connection:
            connection.execute("""UPDATE browser_worker_runs SET status='complete',finished_at=CURRENT_TIMESTAMP,sources_checked=?,urls_discovered=?,articles_fetched=?,http_extractions=?,browser_fallbacks=?,unchanged_skipped=?,extraction_failures=? WHERE run_id=?""", (checked, discovered, fetched, http_count, browser_count, unchanged, failures, run_id))
            connection.commit()
        return summary


def source_is_due(source: dict[str, object], *, now: datetime | None = None) -> bool:
    last_crawl = str(source.get("last_crawl") or "").strip()
    if not last_crawl:
        return True
    try:
        previous = datetime.fromisoformat(last_crawl.replace("Z", "+00:00"))
    except ValueError:
        return True
    if previous.tzinfo is None:
        previous = previous.replace(tzinfo=UTC)
    interval = max(1, int(source.get("crawl_frequency_minutes") or 60))
    return (now or datetime.now(UTC)) >= previous + timedelta(minutes=interval)


def run_continuously(worker_factory: Callable[[], BrowserWorker] = BrowserWorker, *, interval_seconds: int | None = None) -> None:
    interval = max(60, int(interval_seconds or os.getenv("AD_DOCTOR_BROWSER_CYCLE_SECONDS", "900")))
    while True:
        worker_factory().run_once()
        time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Approved-source Ad Doctor browser worker")
    parser.add_argument("--once", action="store_true", help="Run one bounded crawl cycle")
    args = parser.parse_args(argv)
    if args.once:
        print(json.dumps(asdict(BrowserWorker().run_once()), sort_keys=True))
        return 0
    run_continuously()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
