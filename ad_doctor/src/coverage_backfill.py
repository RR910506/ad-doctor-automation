from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import re
from typing import Any
from urllib.parse import quote_plus, urlparse

from src.article_fetcher import parse_candidate
from src.article_parser import (
    build_request_headers,
    extract_article_link_candidates,
    is_valid_parsed_article,
    request_with_retries,
)
from src.config import DATABASE_PATH
from src.db import (
    get_article_id_by_url,
    get_connection,
    link_topic_source,
    list_active_sources,
    list_articles,
    list_topics,
    list_topic_scores,
    list_topic_sources,
    log_run_event,
    upsert_article,
)
from src.topic_matcher import is_same_news_event, named_event_key, topic_tokens


@dataclass(frozen=True)
class CoverageBackfillSummary:
    run_id: str
    topics_checked: int
    source_searches: int
    article_candidates_seen: int
    matched_articles: int
    inserted: int
    updated: int
    failed: int
    messages: list[str]


@dataclass(frozen=True)
class StoredCoverageExpansionSummary:
    run_id: str
    topics_checked: int
    articles_checked: int
    links_added: int


def backfill_near_qualified_topic_coverage(
    database_path=DATABASE_PATH,
    run_id: str | None = None,
    topic_limit: int = 5,
    max_search_sources_per_topic: int | None = None,
    max_coverage_gap: int | None = 1,
    search_urls_per_source: int = 3,
    source_time_budget_seconds: int = 20,
    topic_contains: str | None = None,
    named_events_only: bool = False,
) -> CoverageBackfillSummary:
    run_id = run_id or datetime.now(UTC).strftime("run_%Y%m%d_%H%M%S")
    source_searches = 0
    article_candidates_seen = 0
    matched_articles = 0
    inserted = 0
    updated = 0
    failed = 0
    messages: list[str] = []

    with closing(get_connection(database_path)) as connection:
        topics = topics_needing_coverage(
            connection,
            topic_limit=topic_limit,
            max_coverage_gap=max_coverage_gap,
        )
        if topic_contains:
            topic_filter = topic_contains.lower()
            topics = [topic for topic in topics if topic_filter in str(topic["exact_topic"]).lower()]
        if named_events_only:
            topics = [topic for topic in topics if named_event_key(str(topic["exact_topic"]))]
        sources = list_active_sources(connection)
        sources_by_id = {int(source["id"]): source for source in sources}
        log_run_event(
            connection,
            run_id,
            "phase_5_coverage_backfill",
            "start",
            f"Starting approved-domain coverage expansion for {len(topics)} topic(s).",
        )
        connection.commit()

        for topic in topics:
            topic_id = int(topic["topic_id"])
            topic_text = str(topic["exact_topic"])
            existing_sources = {
                int(row["source_id"]) for row in list_topic_sources(connection, topic_id)
            }
            gap = max(
                0,
                int(topic["minimum_required_sources"]) - int(topic["unique_source_coverage_count"]),
            )
            if gap <= 0:
                continue
            log_run_event(
                connection,
                run_id,
                "phase_5_coverage_backfill",
                "topic_start",
                f"Expanding coverage for topic: {topic_text}",
                auto_commit=True,
            )
            local_matches = link_existing_same_event_articles(
                connection=connection,
                topic_id=topic_id,
                topic=topic_text,
                existing_sources=existing_sources,
            )
            if local_matches:
                matched_articles += local_matches
                gap = max(0, gap - local_matches)
                existing_sources = {
                    int(row["source_id"]) for row in list_topic_sources(connection, topic_id)
                }
                messages.append(f"Linked {local_matches} existing stored article(s) for topic '{topic_text}'.")
            ranked_remaining_sources = rank_backfill_sources([
                source for source_id, source in sources_by_id.items() if source_id not in existing_sources
            ])
            remaining_sources = (
                ranked_remaining_sources
                if max_search_sources_per_topic is None
                else ranked_remaining_sources[:max_search_sources_per_topic]
            )

            for source in remaining_sources:
                source_started = datetime.now(UTC).timestamp()
                source_matched = False
                no_headline_parse_budget = 1
                log_run_event(
                    connection,
                    run_id,
                    "phase_5_coverage_backfill",
                    "source_start",
                    f"Searching {source['source_name']} for topic: {topic_text}",
                    auto_commit=True,
                )
                for search_url in build_source_search_urls(str(source["website_url"]), topic_text)[:search_urls_per_source]:
                    if datetime.now(UTC).timestamp() - source_started > source_time_budget_seconds:
                        messages.append(f"Stopped {source['source_name']} topic search after {source_time_budget_seconds}s budget.")
                        break
                    source_searches += 1
                    try:
                        candidates = discover_search_candidates(
                            source_url=str(source["website_url"]),
                            search_url=search_url,
                        )
                    except Exception as exc:
                        failed += 1
                        messages.append(f"Search failed for {source['source_name']}: {search_url} ({exc})")
                        continue
                    for candidate in candidates:
                        article_candidates_seen += 1
                        if not is_candidate_promising(topic_text, candidate):
                            continue
                        if not candidate.get("headline") and no_headline_parse_budget <= 0:
                            continue
                        if not candidate.get("headline"):
                            no_headline_parse_budget -= 1
                        try:
                            parsed = parse_candidate(candidate)
                            if not is_valid_backfill_match(topic_text, parsed):
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
                            article_id = get_article_id_by_url(connection, str(article["article_url"]))
                            if article_id is not None:
                                link_topic_source(
                                    connection,
                                    topic_id=topic_id,
                                    source_id=int(source["id"]),
                                    article_id=article_id,
                                    coverage_url=str(article["article_url"]),
                                )
                            inserted += 1 if outcome == "inserted" else 0
                            updated += 1 if outcome == "updated" else 0
                            matched_articles += 1
                            gap -= 1
                            source_matched = True
                            messages.append(
                                f"Matched {source['source_name']} for topic '{topic_text}': {article['article_url']}"
                            )
                            break
                        except Exception as exc:
                            failed += 1
                            messages.append(
                                f"Candidate failed for {source['source_name']}: {candidate.get('article_url')} ({exc})"
                            )
                    if source_matched:
                        break
                if not source_matched:
                    try:
                        if datetime.now(UTC).timestamp() - source_started > source_time_budget_seconds:
                            raise TimeoutError(f"Source topic search budget reached after {source_time_budget_seconds}s")
                        sitemap_candidates = discover_sitemap_candidates(
                            source_url=str(source["website_url"]),
                            topic=topic_text,
                        )
                        source_searches += 1
                    except Exception as exc:
                        failed += 1
                        messages.append(f"Sitemap search failed for {source['source_name']}: {exc}")
                        sitemap_candidates = []
                    for candidate in sitemap_candidates:
                        if datetime.now(UTC).timestamp() - source_started > source_time_budget_seconds:
                            messages.append(f"Stopped {source['source_name']} sitemap scan after {source_time_budget_seconds}s budget.")
                            break
                        article_candidates_seen += 1
                        try:
                            parsed = parse_candidate(candidate)
                            if not is_valid_backfill_match(topic_text, parsed):
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
                            article_id = get_article_id_by_url(connection, str(article["article_url"]))
                            if article_id is not None:
                                link_topic_source(
                                    connection,
                                    topic_id=topic_id,
                                    source_id=int(source["id"]),
                                    article_id=article_id,
                                    coverage_url=str(article["article_url"]),
                                )
                            inserted += 1 if outcome == "inserted" else 0
                            updated += 1 if outcome == "updated" else 0
                            matched_articles += 1
                            gap -= 1
                            messages.append(
                                f"Matched {source['source_name']} via sitemap for topic '{topic_text}': {article['article_url']}"
                            )
                            break
                        except Exception as exc:
                            failed += 1
                            messages.append(
                                f"Sitemap candidate failed for {source['source_name']}: {candidate.get('article_url')} ({exc})"
                            )
                log_run_event(
                    connection,
                    run_id,
                    "phase_5_coverage_backfill",
                    "source_complete",
                    f"Finished {source['source_name']} for topic: {topic_text}. Matched: {source_matched}.",
                    auto_commit=True,
                )

        summary = CoverageBackfillSummary(
            run_id=run_id,
            topics_checked=len(topics),
            source_searches=source_searches,
            article_candidates_seen=article_candidates_seen,
            matched_articles=matched_articles,
            inserted=inserted,
            updated=updated,
            failed=failed,
            messages=messages,
        )
        log_run_event(
            connection,
            run_id,
            "phase_5_coverage_backfill",
            "complete",
            (
                f"Coverage backfill complete: {matched_articles} matched article(s), "
                f"{source_searches} approved-domain search page(s), {failed} failed."
            ),
            json.dumps(summary.__dict__),
        )
        connection.commit()
        return summary


def expand_topic_coverage_from_stored_articles(
    database_path=DATABASE_PATH,
    run_id: str | None = None,
    topic_limit: int | None = None,
) -> StoredCoverageExpansionSummary:
    run_id = run_id or datetime.now(UTC).strftime("run_%Y%m%d_%H%M%S")
    links_added = 0

    with closing(get_connection(database_path)) as connection:
        topics = list_topics(connection, limit=topic_limit)
        articles = prepare_article_match_records(list_articles(connection, limit=None))
        log_run_event(
            connection,
            run_id,
            "phase_5_stored_coverage_expansion",
            "start",
            f"Expanding {len(topics)} topic(s) across {len(articles)} stored approved-source article(s).",
            auto_commit=True,
        )

        for topic in topics:
            existing_sources = {
                int(row["source_id"])
                for row in list_topic_sources(connection, int(topic["id"]))
            }
            links_added += link_existing_same_event_articles(
                connection=connection,
                topic_id=int(topic["id"]),
                topic=str(topic["exact_topic"]),
                existing_sources=existing_sources,
                articles=articles,
            )

        summary = StoredCoverageExpansionSummary(
            run_id=run_id,
            topics_checked=len(topics),
            articles_checked=len(articles),
            links_added=links_added,
        )
        log_run_event(
            connection,
            run_id,
            "phase_5_stored_coverage_expansion",
            "complete",
            f"Stored coverage expansion complete: {links_added} new topic-source link(s).",
            json.dumps(summary.__dict__),
            auto_commit=True,
        )
        return summary


def link_existing_same_event_articles(
    connection,
    topic_id: int,
    topic: str,
    existing_sources: set[int],
    articles: list[dict[str, Any]] | None = None,
) -> int:
    linked = 0
    articles = (
        articles
        if articles is not None and (not articles or "_match_evidence" in articles[0])
        else prepare_article_match_records(articles if articles is not None else list_articles(connection, limit=None))
    )
    topic_key = named_event_key(topic)
    topic_match_tokens = topic_tokens(topic)
    for article in articles:
        source_id = int(article["source_id"])
        if source_id in existing_sources:
            continue
        evidence = str(article.get("_match_evidence") or "")
        if not should_compare_topic_to_article(
            topic_key=topic_key,
            topic_match_tokens=topic_match_tokens,
            article_match_key=article.get("_match_event_key"),
            article_match_tokens=article.get("_match_tokens"),
        ):
            continue
        if not is_same_news_event(topic, evidence):
            continue
        link_topic_source(
            connection,
            topic_id=topic_id,
            source_id=source_id,
            article_id=int(article["id"]),
            coverage_url=str(article["article_url"]),
        )
        existing_sources.add(source_id)
        linked += 1
    if linked:
        connection.commit()
    return linked


def prepare_article_match_records(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prepared = []
    for article in articles:
        evidence = " ".join(
            str(article.get(key) or "")
            for key in ("headline", "meta_description", "article_url")
        )
        record = dict(article)
        record["_match_evidence"] = evidence
        record["_match_tokens"] = topic_tokens(evidence)
        record["_match_event_key"] = named_event_key(evidence)
        prepared.append(record)
    return prepared


def should_compare_topic_to_article(
    topic_key: str | None,
    topic_match_tokens: set[str],
    article_match_key: object,
    article_match_tokens: object,
) -> bool:
    if topic_key and article_match_key == topic_key:
        return True
    if not isinstance(article_match_tokens, set):
        return False
    shared_tokens = topic_match_tokens & article_match_tokens
    return len(shared_tokens) >= 2


def near_qualified_topics(connection, topic_limit: int = 5) -> list[dict[str, Any]]:
    return topics_needing_coverage(connection, topic_limit=topic_limit, max_coverage_gap=1)


def topics_needing_coverage(
    connection,
    topic_limit: int = 5,
    max_coverage_gap: int | None = 1,
) -> list[dict[str, Any]]:
    topics = []
    for topic in list_topic_scores(connection):
        required = int(topic["minimum_required_sources"])
        coverage = int(topic["unique_source_coverage_count"])
        gap = required - coverage
        if required > 0 and coverage > 0 and gap > 0 and (max_coverage_gap is None or gap <= max_coverage_gap):
            topics.append(topic)
    return topics[:topic_limit]


def build_source_search_urls(source_url: str, topic: str) -> list[str]:
    parsed = urlparse(source_url)
    root_url = f"{parsed.scheme}://{parsed.netloc}"
    candidates = []
    for query_text in build_search_queries(topic):
        query = quote_plus(query_text)
        candidates.extend(
            [
                f"{root_url}/?s={query}",
                f"{root_url}/search?q={query}",
                f"{root_url}/search?query={query}",
                f"{source_url.rstrip('/')}/?s={query}",
            ]
        )
    return list(dict.fromkeys(candidates))


def build_search_queries(topic: str) -> list[str]:
    cleaned_topic = normalize_search_query_text(topic)
    event_key = named_event_key(cleaned_topic)
    compact_words = [
        word
        for word in re.findall(r"[A-Za-z0-9]+", cleaned_topic)
        if word.lower()
        not in {
            "the",
            "and",
            "with",
            "from",
            "into",
            "head",
            "go",
            "goes",
            "launch",
            "launches",
            "launched",
            "global",
        }
    ]
    compact_query = " ".join(compact_words[:8])
    queries = [event_key] if event_key else []
    queries.append(cleaned_topic)
    if compact_query and compact_query.lower() != cleaned_topic.lower():
        queries.append(compact_query)
    return list(dict.fromkeys(query for query in queries if query))


def normalize_search_query_text(topic: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9]+", " ", topic)).strip()


def rank_backfill_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    priority_fragments = [
        "adweek",
        "campaign global",
        "campaign india",
        "marketing dive",
        "the drum",
        "marketing interactive",
        "media news",
        "medianews",
        "exchange4media",
        "et brand",
        "afaqs",
        "social samosa",
        "little black book",
        "lbb",
        "branding",
        "ads of the world",
    ]

    def priority(source: dict[str, Any]) -> tuple[int, str]:
        text = f"{source.get('source_name', '')} {source.get('website_url', '')}".lower()
        for index, fragment in enumerate(priority_fragments):
            if fragment in text:
                return index, str(source.get("source_name", ""))
        return len(priority_fragments), str(source.get("source_name", ""))

    return sorted(sources, key=priority)


def discover_search_candidates(source_url: str, search_url: str) -> list[dict[str, str | None]]:
    response = request_with_retries(
        search_url,
        timeout=6,
        headers=build_request_headers(referer=root_from_url(source_url)),
        attempts=2,
    )
    return extract_article_link_candidates(source_url, response.text, limit=5)


def discover_sitemap_candidates(source_url: str, topic: str) -> list[dict[str, str | None]]:
    requests = require_requests()
    root_url = root_from_url(source_url)
    sitemap_urls = [
        f"{root_url}/sitemap.xml",
        f"{root_url}/sitemap_index.xml",
        f"{root_url}/sitemap-index.xml",
        f"{root_url}/news-sitemap.xml",
    ]
    candidates: list[dict[str, str | None]] = []
    fetched_sitemaps: set[str] = set()

    for sitemap_url in sitemap_urls:
        try:
            candidates.extend(
                discover_sitemap_candidates_from_url(
                    requests=requests,
                    source_url=source_url,
                    sitemap_url=sitemap_url,
                    topic=topic,
                    fetched_sitemaps=fetched_sitemaps,
                )
            )
        except Exception:
            continue
        if candidates:
            break
    return candidates[:5]


def discover_sitemap_candidates_from_url(
    requests,
    source_url: str,
    sitemap_url: str,
    topic: str,
    fetched_sitemaps: set[str],
) -> list[dict[str, str | None]]:
    if sitemap_url in fetched_sitemaps:
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
    if child_sitemaps:
        candidates: list[dict[str, str | None]] = []
        for child_sitemap in rank_child_sitemaps(child_sitemaps)[:8]:
            try:
                candidates.extend(
                    discover_sitemap_candidates_from_url(
                        requests=requests,
                        source_url=source_url,
                        sitemap_url=child_sitemap,
                        topic=topic,
                        fetched_sitemaps=fetched_sitemaps,
                    )
                )
            except Exception:
                continue
            if candidates:
                break
        return candidates

    candidates = []
    source_root = root_from_url(source_url)
    for loc in locs:
        if not loc.startswith(source_root):
            continue
        headline = headline_from_url(loc)
        candidate = {"article_url": loc, "headline": headline, "published_date": None}
        if is_candidate_promising(topic, candidate):
            candidates.append(candidate)
        if len(candidates) >= 5:
            break
    return candidates


def extract_sitemap_locs(xml_text: str) -> list[str]:
    return [match.strip() for match in re.findall(r"<loc>\s*(.*?)\s*</loc>", xml_text, flags=re.IGNORECASE)]


def rank_child_sitemaps(sitemap_urls: list[str]) -> list[str]:
    priority_fragments = [
        "cannes",
        "events",
        "awards",
        "agencies",
        "brand",
        "marketing",
        "media",
        "news",
        "creativity",
        "social",
        "campaign",
    ]

    def priority(url: str) -> tuple[int, str]:
        lowered = url.lower()
        for index, fragment in enumerate(priority_fragments):
            if fragment in lowered:
                return index, url
        return len(priority_fragments), url

    return sorted(sitemap_urls, key=priority)


def headline_from_url(url: str) -> str | None:
    slug = urlparse(url).path.rstrip("/").split("/")[-1]
    if not slug:
        return None
    return " ".join(part for part in re.split(r"[-_]+", slug) if part).strip() or None


def root_from_url(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def is_candidate_promising(topic: str, candidate: dict[str, str | None]) -> bool:
    headline = candidate.get("headline")
    if not headline:
        return True
    return is_same_news_event(topic, headline)


def is_valid_backfill_match(topic: str, parsed_article) -> bool:
    if not is_valid_parsed_article(parsed_article):
        return False
    evidence_strings = [
        parsed_article.headline or "",
        parsed_article.meta_description or "",
    ]
    text = parsed_article.article_text or ""
    if text:
        evidence_strings.append(text[:1000])
    return any(is_same_news_event(topic, evidence) for evidence in evidence_strings if evidence)


def require_requests():
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError("Install requests to search approved source domains.") from exc
    return requests
