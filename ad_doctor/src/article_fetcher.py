from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import re
import time
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

from src.article_parser import (
    ParsedArticle,
    build_request_headers,
    discover_feed_links,
    extract_article_link_candidates,
    extract_link_candidates,
    fetch_homepage_html,
    is_likely_article_url,
    is_same_domain_url,
    is_valid_parsed_article,
    parse_article_url,
    request_with_retries,
)
from src.config import DATABASE_PATH
from src.db import (
    get_connection,
    initialize_database,
    list_active_sources,
    log_run_event,
    update_source_rss_url,
    upsert_article,
)


@dataclass(frozen=True)
class FetchSummary:
    run_id: str
    sources_checked: int
    articles_seen: int
    inserted: int
    updated: int
    failed: int
    messages: list[str]
    duration_seconds: float = 0.0


def fetch_articles(
    max_articles_per_source: int | None = 5,
    database_path=DATABASE_PATH,
    run_id: str | None = None,
    fetch_full_text: bool = False,
    source_time_budget_seconds: int = 20,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    exhaustive_scan: bool = False,
) -> FetchSummary:
    initialize_database(database_path=database_path)
    run_id = run_id or build_run_id()
    run_started_at = time.monotonic()
    inserted = 0
    updated = 0
    failed = 0
    articles_seen = 0
    messages: list[str] = []

    with closing(get_connection(database_path)) as connection:
        sources = list_active_sources(connection)
        log_run_event(
            connection,
            run_id,
            "phase_3_article_fetch",
            "start",
            (
                f"Starting article fetch for {len(sources)} active source(s), "
                f"{'all discoverable articles' if max_articles_per_source is None else f'up to {max_articles_per_source} article(s) each'}."
            ),
            auto_commit=True,
        )
        emit_progress(
            progress_callback,
            "start",
            (
                f"Starting article fetch for {len(sources)} active source(s), "
                f"{'all discoverable articles' if max_articles_per_source is None else f'up to {max_articles_per_source} article(s) each'}."
            ),
            {"total_sources": len(sources), "completed_sources": 0},
        )

        for source_index, source in enumerate(sources, start=1):
            source_started_at = time.monotonic()
            source_name = str(source["source_name"])
            source_seen = 0
            source_valid = 0
            log_run_event(
                connection,
                run_id,
                "phase_3_article_fetch",
                "source_start",
                f"Checking source: {source_name}.",
                auto_commit=True,
            )
            emit_progress(
                progress_callback,
                "source_start",
                f"Checking {source_name} ({source_index}/{len(sources)}).",
                {
                    "source_index": source_index,
                    "total_sources": len(sources),
                    "completed_sources": source_index - 1,
                    "source_name": source_name,
                },
            )
            try:
                valid_articles_for_source = 0
                candidates = discover_article_candidates(
                    source,
                    max_articles_per_source=max_articles_per_source,
                    candidate_limit=calculate_candidate_limit(max_articles_per_source),
                    connection=connection,
                    exhaustive_scan=exhaustive_scan,
                    source_deadline_monotonic=source_started_at + source_time_budget_seconds,
                )
                if not candidates:
                    message = f"No article candidates found for {source_name}."
                    messages.append(message)
                    log_run_event(connection, run_id, "phase_3_article_fetch", "warning", message, auto_commit=True)
                    emit_progress(progress_callback, "warning", message, {"source_name": source_name})
                    continue

                for candidate in candidates:
                    if time.monotonic() - source_started_at > source_time_budget_seconds:
                        message = f"Stopped source {source_name} after {source_time_budget_seconds}s budget; kept {valid_articles_for_source} valid article(s)."
                        messages.append(message)
                        log_run_event(
                            connection,
                            run_id,
                            "phase_3_article_fetch",
                            "source_timed_out",
                            message,
                            json.dumps(
                                {
                                    "source_id": source["id"],
                                    "source_name": source_name,
                                    "valid_articles": valid_articles_for_source,
                                    "elapsed_seconds": round(time.monotonic() - source_started_at, 2),
                                }
                            ),
                            auto_commit=True,
                        )
                        emit_progress(
                            progress_callback,
                            "source_timed_out",
                            message,
                            {
                                "source_name": source_name,
                                "valid_articles": valid_articles_for_source,
                                "elapsed_seconds": round(time.monotonic() - source_started_at, 2),
                            },
                        )
                        break
                    articles_seen += 1
                    source_seen += 1
                    try:
                        parsed = parse_candidate(candidate, fetch_full_text=fetch_full_text)
                        if not is_valid_parsed_article(parsed):
                            message = f"Skipped low-quality article candidate for {source_name}: {candidate.get('article_url')}"
                            messages.append(message)
                            log_run_event(connection, run_id, "phase_3_article_fetch", "warning", message, auto_commit=True)
                            emit_progress(progress_callback, "warning", message, {"source_name": source_name})
                            continue
                        article = parsed.to_record()
                        article.update(
                            {
                                "source_id": source["id"],
                                "source_name": source["source_name"],
                                "source_url": source["website_url"],
                                "fetch_run_id": run_id,
                            }
                        )
                        outcome = upsert_article(connection, article)
                        if outcome == "inserted":
                            inserted += 1
                        else:
                            updated += 1
                        valid_articles_for_source += 1
                        source_valid += 1
                        if max_articles_per_source is not None and valid_articles_for_source >= max_articles_per_source:
                            break
                    except Exception as exc:
                        failed += 1
                        message = f"Failed article for {source_name}: {candidate.get('article_url')} ({exc})"
                        messages.append(message)
                        log_run_event(connection, run_id, "phase_3_article_fetch", "error", message, auto_commit=True)
                        emit_progress(progress_callback, "error", message, {"source_name": source_name})
            except Exception as exc:
                failed += 1
                message = f"Failed source {source_name}: {exc}"
                messages.append(message)
                log_run_event(
                    connection,
                    run_id,
                    "phase_3_article_fetch",
                    "source_failed",
                    message,
                    json.dumps({"source_id": source["id"], "source_name": source_name}),
                    auto_commit=True,
                )
                emit_progress(progress_callback, "source_failed", message, {"source_name": source_name})
            finally:
                elapsed_seconds = round(time.monotonic() - source_started_at, 2)
                source_complete_message = (
                    f"Finished {source_name}: {source_valid} valid article(s), "
                    f"{source_seen} candidate(s) checked in {elapsed_seconds}s."
                )
                log_run_event(
                    connection,
                    run_id,
                    "phase_3_article_fetch",
                    "source_complete",
                    source_complete_message,
                    json.dumps(
                        {
                            "source_id": source["id"],
                            "source_name": source_name,
                            "valid_articles": source_valid,
                            "candidates_checked": source_seen,
                            "elapsed_seconds": elapsed_seconds,
                        }
                    ),
                    auto_commit=True,
                )
                emit_progress(
                    progress_callback,
                    "source_complete",
                    source_complete_message,
                    {
                        "source_name": source_name,
                        "source_index": source_index,
                        "total_sources": len(sources),
                        "completed_sources": source_index,
                        "valid_articles": source_valid,
                        "candidates_checked": source_seen,
                        "elapsed_seconds": elapsed_seconds,
                    },
                )

        summary = FetchSummary(
            run_id=run_id,
            sources_checked=len(sources),
            articles_seen=articles_seen,
            inserted=inserted,
            updated=updated,
            failed=failed,
            messages=messages,
            duration_seconds=round(time.monotonic() - run_started_at, 2),
        )
        log_run_event(
            connection,
            run_id,
            "phase_3_article_fetch",
            "complete",
            f"Article fetch complete in {summary.duration_seconds}s: {inserted} inserted, {updated} updated, {failed} failed.",
            json.dumps(summary.__dict__),
            auto_commit=True,
        )
        emit_progress(
            progress_callback,
            "complete",
            f"Article fetch complete in {summary.duration_seconds}s: {inserted} inserted, {updated} updated, {failed} failed.",
            {"total_sources": len(sources), "completed_sources": len(sources)},
        )
        return summary


def emit_progress(
    progress_callback: Callable[[dict[str, Any]], None] | None,
    event_type: str,
    message: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    if progress_callback is None:
        return
    payload: dict[str, Any] = {"event_type": event_type, "message": message}
    if metadata:
        payload.update(metadata)
    progress_callback(payload)


def discover_article_candidates(
    source: dict[str, Any],
    max_articles_per_source: int | None = 5,
    candidate_limit: int | None = None,
    connection=None,
    exhaustive_scan: bool = False,
    source_deadline_monotonic: float | None = None,
) -> list[dict[str, str | None]]:
    candidates: list[dict[str, str | None]] = []
    homepage_html: str | None = None
    candidate_limit = calculate_candidate_limit(max_articles_per_source) if candidate_limit is None else candidate_limit

    feed_candidates = build_feed_candidates(source)
    feed_candidates_to_try = feed_candidates[:5] if exhaustive_scan else feed_candidates[:3]
    for feed_url in feed_candidates_to_try:
        if not has_time_remaining(source_deadline_monotonic):
            break
        try:
            candidates.extend(fetch_rss_candidates(feed_url, max_articles_per_source=candidate_limit))
        except Exception:
            continue
        if candidates and not exhaustive_scan:
            break

    if not candidates or exhaustive_scan:
        if not has_time_remaining(source_deadline_monotonic):
            return dedupe_candidates(candidates)
        homepage_html = fetch_homepage_html(str(source["website_url"]), timeout=4)
        candidates.extend(
            extract_article_link_candidates(
                source_url=str(source["website_url"]),
                html=homepage_html,
                limit=candidate_limit,
            )
        )
        discovered_feed_limit = 5 if exhaustive_scan else 2
        for feed_url in discover_feed_links(str(source["website_url"]), homepage_html, limit=discovered_feed_limit):
            if not has_time_remaining(source_deadline_monotonic):
                break
            try:
                candidates.extend(fetch_rss_candidates(feed_url, max_articles_per_source=candidate_limit))
                if candidates and not exhaustive_scan:
                    if connection is not None:
                        update_source_rss_url(connection, int(source["id"]), feed_url)
                    break
                if candidates and connection is not None:
                    update_source_rss_url(connection, int(source["id"]), feed_url)
            except Exception:
                continue

    if exhaustive_scan:
        try:
            if has_time_remaining(source_deadline_monotonic):
                candidates.extend(
                    crawl_source_article_candidates(
                        source_url=str(source["website_url"]),
                        limit=candidate_limit,
                        source_deadline_monotonic=source_deadline_monotonic,
                    )
                )
            if has_time_remaining(source_deadline_monotonic):
                candidates.extend(
                    discover_sitemap_article_candidates(
                        source_url=str(source["website_url"]),
                        limit=candidate_limit,
                        source_deadline_monotonic=source_deadline_monotonic,
                    )
                )
        except Exception:
            pass

    if not candidates:
        if homepage_html is None and has_time_remaining(source_deadline_monotonic):
            homepage_html = fetch_homepage_html(str(source["website_url"]), timeout=4)
        if homepage_html is not None:
            candidates.extend(
                extract_article_link_candidates(
                    source_url=str(source["website_url"]),
                    html=homepage_html,
                    limit=candidate_limit,
                )
            )

    deduped = dedupe_candidates(candidates)
    return deduped if candidate_limit is None else deduped[:candidate_limit]


def has_time_remaining(source_deadline_monotonic: float | None, reserve_seconds: float = 1.0) -> bool:
    return source_deadline_monotonic is None or time.monotonic() + reserve_seconds < source_deadline_monotonic


def calculate_candidate_limit(max_articles_per_source: int | None) -> int | None:
    if max_articles_per_source is None:
        return None
    return min(max(max_articles_per_source + 4, max_articles_per_source * 2), 15)


def build_feed_candidates(source: dict[str, Any]) -> list[str]:
    source_url = str(source["website_url"]).rstrip("/")
    feed_candidates: list[str] = []
    if source.get("rss_url"):
        feed_candidates.append(str(source["rss_url"]))

    parsed = urlparse(source_url)
    root_url = f"{parsed.scheme}://{parsed.netloc}"
    feed_candidates.extend(
        [
            urljoin(source_url + "/", "feed"),
            urljoin(source_url + "/", "feed/"),
            urljoin(source_url + "/", "rss"),
            urljoin(source_url + "/", "rss.xml"),
            urljoin(source_url + "/", "feed.xml"),
            urljoin(root_url + "/", "feed"),
            urljoin(root_url + "/", "feed/"),
            urljoin(root_url + "/", "rss"),
            urljoin(root_url + "/", "rss.xml"),
            urljoin(root_url + "/", "feed.xml"),
            urljoin(root_url + "/", "news/feed"),
            urljoin(root_url + "/", "news/feed/"),
            f"{root_url}/?feed=rss2",
        ]
    )
    return list(dict.fromkeys(feed_candidates))


def require_requests():
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError("Install requests to fetch source pages.") from exc
    return requests


def fetch_rss_candidates(feed_url: str, max_articles_per_source: int | None = 5) -> list[dict[str, str | None]]:
    try:
        import feedparser
    except ImportError as exc:
        raise RuntimeError("Install feedparser to fetch RSS feeds.") from exc

    response = request_with_retries(
        feed_url,
        timeout=4,
        headers=build_request_headers(referer=root_from_url(feed_url)),
        attempts=2,
    )
    parsed_feed = feedparser.parse(response.content)
    candidates: list[dict[str, str | None]] = []
    entries = parsed_feed.entries if max_articles_per_source is None else parsed_feed.entries[:max_articles_per_source]
    for entry in entries:
        article_url = getattr(entry, "link", None)
        if not article_url:
            continue
        headline = getattr(entry, "title", None)
        if not is_likely_article_url(article_url, str(headline or "")):
            continue
        candidates.append(
            {
                "article_url": article_url,
                "headline": headline,
                "published_date": getattr(entry, "published", None) or getattr(entry, "updated", None),
            }
        )
    return candidates


def discover_sitemap_article_candidates(
    source_url: str,
    limit: int | None = None,
    source_deadline_monotonic: float | None = None,
) -> list[dict[str, str | None]]:
    requests = require_requests()
    root_url = root_from_url(source_url)
    sitemap_urls = [
        f"{root_url}/sitemap.xml",
        f"{root_url}/sitemap_index.xml",
        f"{root_url}/sitemap-index.xml",
        f"{root_url}/news-sitemap.xml",
    ]
    sitemap_urls.extend(discover_robots_sitemaps(root_url, source_deadline_monotonic))
    sitemap_urls = list(dict.fromkeys(sitemap_urls))
    candidates: list[dict[str, str | None]] = []
    fetched_sitemaps: set[str] = set()
    for sitemap_url in sitemap_urls:
        if not has_time_remaining(source_deadline_monotonic):
            break
        try:
            candidates.extend(
                discover_sitemap_article_candidates_from_url(
                    requests=requests,
                    source_url=source_url,
                    sitemap_url=sitemap_url,
                    fetched_sitemaps=fetched_sitemaps,
                    limit=None if limit is None else max(0, limit - len(candidates)),
                    source_deadline_monotonic=source_deadline_monotonic,
                )
            )
        except Exception:
            continue
        if limit is not None and len(candidates) >= limit:
            break
    return candidates if limit is None else candidates[:limit]


def discover_sitemap_article_candidates_from_url(
    requests,
    source_url: str,
    sitemap_url: str,
    fetched_sitemaps: set[str],
    limit: int | None = None,
    source_deadline_monotonic: float | None = None,
) -> list[dict[str, str | None]]:
    if sitemap_url in fetched_sitemaps:
        return []
    if not has_time_remaining(source_deadline_monotonic):
        return []
    fetched_sitemaps.add(sitemap_url)
    response = request_with_retries(
        sitemap_url,
        timeout=8,
        headers=build_request_headers(referer=root_from_url(source_url)),
        attempts=2,
    )
    locs = extract_sitemap_locs(response.text)
    child_sitemaps = [loc for loc in locs if "sitemap" in loc.lower()]
    candidates: list[dict[str, str | None]] = []
    if child_sitemaps:
        for child_sitemap in rank_child_sitemaps(child_sitemaps):
            if not has_time_remaining(source_deadline_monotonic):
                break
            try:
                candidates.extend(
                    discover_sitemap_article_candidates_from_url(
                        requests=requests,
                        source_url=source_url,
                        sitemap_url=child_sitemap,
                        fetched_sitemaps=fetched_sitemaps,
                        limit=None if limit is None else max(0, limit - len(candidates)),
                        source_deadline_monotonic=source_deadline_monotonic,
                    )
                )
            except Exception:
                continue
            if limit is not None and len(candidates) >= limit:
                break
        return candidates if limit is None else candidates[:limit]

    source_root = root_from_url(source_url)
    for loc in locs:
        if not has_time_remaining(source_deadline_monotonic, reserve_seconds=0.2):
            break
        if not loc.startswith(source_root):
            continue
        headline = headline_from_url(loc)
        if not is_likely_article_url(loc, headline or "", source_url=source_url):
            continue
        candidates.append({"article_url": loc, "headline": headline, "published_date": None})
        if limit is not None and len(candidates) >= limit:
            break
    return candidates


def crawl_source_article_candidates(
    source_url: str,
    limit: int | None = None,
    page_limit: int = 80,
    source_deadline_monotonic: float | None = None,
) -> list[dict[str, str | None]]:
    requests = require_requests()
    queue = list(dict.fromkeys([root_from_url(source_url), source_url, *build_crawl_seed_urls(source_url)]))
    visited_pages: set[str] = set()
    candidates: dict[str, dict[str, str | None]] = {}

    while queue and len(visited_pages) < page_limit and has_time_remaining(source_deadline_monotonic):
        page_url = queue.pop(0)
        if page_url in visited_pages or not is_same_domain_url(source_url, page_url):
            continue
        visited_pages.add(page_url)
        try:
            response = request_with_retries(
                page_url,
                timeout=6,
                headers=build_request_headers(referer=root_from_url(source_url)),
                attempts=2,
            )
        except Exception:
            continue
        for article_candidate in extract_article_link_candidates(page_url, response.text, limit=None):
            article_url = str(article_candidate["article_url"])
            headline = str(article_candidate.get("headline") or "")
            if not is_same_domain_url(source_url, article_url):
                continue
            if is_listing_page_link(article_url, headline):
                if is_promising_crawl_page(source_url, article_url, headline) and article_url not in visited_pages and article_url not in queue:
                    queue.append(article_url)
                continue
            candidates[article_url] = article_candidate
            if limit is not None and len(candidates) >= limit:
                return list(candidates.values())[:limit]
        for link in extract_link_candidates(page_url, response.text, limit=None):
            link_url = str(link["url"])
            link_text = str(link.get("text") or "")
            if is_listing_page_link(link_url, link_text):
                if is_promising_crawl_page(source_url, link_url, link_text) and link_url not in visited_pages and link_url not in queue:
                    queue.append(link_url)
                continue
            if is_likely_article_url(link_url, link_text, source_url=source_url):
                candidates[link_url] = {
                    "article_url": link_url,
                    "headline": link_text or headline_from_url(link_url),
                    "published_date": None,
                }
                if limit is not None and len(candidates) >= limit:
                    return list(candidates.values())[:limit]
                continue
            if is_promising_crawl_page(source_url, link_url, link_text) and link_url not in visited_pages and link_url not in queue:
                queue.append(link_url)
    values = list(candidates.values())
    return values if limit is None else values[:limit]


def build_crawl_seed_urls(source_url: str) -> list[str]:
    root_url = root_from_url(source_url)
    seed_paths = [
        "news",
        "article",
        "articles",
        "news/agency-news",
        "news/brand-news",
        "news/creativity-news",
        "news/media-news",
        "news/tech-news",
        "news/movers-and-shakers",
        "news/sports-news",
        "us/cannes-lions",
        "us",
        "category/news",
        "category/experiential",
        "category/brand-marketing",
        "category/agencies",
        "category/creativity",
        "category/media",
        "category/technology",
        "events",
        "events-awards",
        "events-awards/cannes-lions",
        "awards",
        "cannes",
        "cannes-lions",
        "topics/cannes",
        "topics/cannes-lions",
        "tag/cannes",
        "tag/cannes-lions",
        "creative",
        "creativity",
        "marketing",
        "media",
        "brands",
        "brand-marketing",
        "agencies",
        "advertising",
    ]
    seeds = [urljoin(root_url + "/", path) for path in seed_paths]
    paged_seeds = []
    for seed in seeds:
        paged_seeds.extend([f"{seed.rstrip('/')}/page/2", f"{seed.rstrip('/')}?page=2"])
    return [*seeds, *paged_seeds]


def is_listing_page_link(candidate_url: str, text: str = "") -> bool:
    lowered_text = text.strip().lower()
    if lowered_text.startswith(("see all", "skip to", "go to")):
        return True
    path = urlparse(candidate_url).path.strip("/").lower()
    listing_paths = (
        "news",
        "news/agency-news",
        "news/brand-news",
        "news/creativity-news",
        "news/media-news",
        "news/tech-news",
        "news/movers-and-shakers",
        "news/sports-news",
        "us/cannes-lions",
        "us",
        "category",
        "in-depth",
    )
    return path in listing_paths


def is_promising_crawl_page(source_url: str, candidate_url: str, text: str = "") -> bool:
    if not is_same_domain_url(source_url, candidate_url):
        return False
    parsed = urlparse(candidate_url)
    path = parsed.path.strip("/").lower()
    if not path:
        return True
    if any(blocked in path for blocked in ("login", "privacy", "terms", "subscribe", "newsletter", "contact", "about", "jobs")):
        return False
    signal_text = f"{path} {text}".lower()
    return any(
        token in signal_text
        for token in (
            "news",
            "article",
            "category",
            "events",
            "awards",
            "cannes",
            "lions",
            "creative",
            "creativity",
            "marketing",
            "media",
            "brand",
            "brands",
            "campaign",
            "experiential",
            "agencies",
            "advertising",
        )
    )


def extract_sitemap_locs(xml_text: str) -> list[str]:
    return [match.strip() for match in re.findall(r"<loc>\s*(.*?)\s*</loc>", xml_text, flags=re.IGNORECASE)]


def discover_robots_sitemaps(
    root_url: str,
    source_deadline_monotonic: float | None = None,
) -> list[str]:
    if not has_time_remaining(source_deadline_monotonic, reserve_seconds=0.5):
        return []
    try:
        response = request_with_retries(
            f"{root_url}/robots.txt",
            timeout=4,
            headers=build_request_headers(referer=root_url),
            attempts=2,
        )
    except Exception:
        return []
    sitemaps = []
    for line in response.text.splitlines():
        if line.lower().startswith("sitemap:"):
            sitemap_url = line.split(":", 1)[1].strip()
            if sitemap_url:
                sitemaps.append(sitemap_url)
    return sitemaps


def headline_from_url(url: str) -> str | None:
    slug = urlparse(url).path.rstrip("/").split("/")[-1]
    if not slug:
        return None
    return " ".join(part for part in re.split(r"[-_]+", slug) if part).strip() or None


def root_from_url(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def rank_child_sitemaps(sitemap_urls: list[str]) -> list[str]:
    priority_fragments = [
        "news",
        "article",
        "post",
        "events",
        "awards",
        "brand",
        "marketing",
        "media",
        "creative",
        "campaign",
    ]

    def priority(url: str) -> tuple[int, str]:
        lowered = url.lower()
        for index, fragment in enumerate(priority_fragments):
            if fragment in lowered:
                return index, url
        return len(priority_fragments), url

    return sorted(sitemap_urls, key=priority)


def parse_candidate(candidate: dict[str, str | None], fetch_full_text: bool = False):
    article_url = str(candidate["article_url"])
    if candidate.get("headline") and not fetch_full_text:
        return ParsedArticle(
            article_url=article_url,
            headline=candidate.get("headline"),
            published_date=candidate.get("published_date"),
            author=None,
            meta_description=None,
            article_text=None,
        )
    try:
        parsed = parse_article_url(article_url, timeout=4)
        if parsed.headline or not candidate.get("headline"):
            return parsed
        from dataclasses import replace

        return replace(parsed, headline=candidate.get("headline"))
    except Exception:
        if candidate.get("headline"):
            return ParsedArticle(
                article_url=article_url,
                headline=candidate.get("headline"),
                published_date=candidate.get("published_date"),
                author=None,
                meta_description=None,
                article_text=None,
            )
        raise


def dedupe_candidates(candidates: list[dict[str, str | None]]) -> list[dict[str, str | None]]:
    deduped: dict[str, dict[str, str | None]] = {}
    for candidate in candidates:
        article_url = candidate.get("article_url")
        if not article_url:
            continue
        if is_listing_page_link(str(article_url), str(candidate.get("headline") or "")):
            continue
        deduped[str(article_url).rstrip("/")] = candidate
    return list(deduped.values())


def build_run_id() -> str:
    return datetime.now(UTC).strftime("run_%Y%m%d_%H%M%S")
