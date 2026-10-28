from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import re

from src.article_parser import is_generic_headline, is_likely_article_url
from src.config import DATABASE_PATH
from src.db import (
    get_connection,
    link_topic_source,
    list_articles,
    list_topics,
    log_run_event,
    upsert_topic,
)
from src.topic_matcher import find_matching_topic, named_event_key, normalize_topic_text


@dataclass(frozen=True)
class TopicExtractionSummary:
    run_id: str
    articles_checked: int
    topics_created_or_matched: int
    linked_coverages: int
    skipped_articles: int


def extract_topics(
    database_path=DATABASE_PATH,
    run_id: str | None = None,
    article_limit: int | None = None,
    article_fetch_run_id: str | None = None,
    include_demo_articles: bool = False,
) -> TopicExtractionSummary:
    run_id = run_id or datetime.now(UTC).strftime("run_%Y%m%d_%H%M%S")
    topics_created_or_matched = 0
    linked_coverages = 0
    skipped_articles = 0

    with closing(get_connection(database_path)) as connection:
        articles = list_articles(
            connection,
            limit=article_limit,
            fetch_run_id=article_fetch_run_id,
        )
        existing_topics = list_topics(connection, limit=None)
        log_run_event(
            connection,
            run_id,
            "phase_4_topic_extraction",
            "start",
            f"Starting exact topic extraction for {len(articles)} article(s).",
        )

        for article_index, article in enumerate(articles, start=1):
            if not include_demo_articles and is_demo_article(article):
                skipped_articles += 1
                continue
            raw_exact_topic = extract_exact_topic_from_article(article)
            if not raw_exact_topic:
                skipped_articles += 1
                continue
            if not is_valid_exact_topic(raw_exact_topic, article, include_demo_articles=include_demo_articles):
                skipped_articles += 1
                continue

            exact_topic = canonical_topic_title(raw_exact_topic) or raw_exact_topic
            matching_topic = find_matching_topic(raw_exact_topic, existing_topics)
            if matching_topic:
                topic_id = int(matching_topic["id"])
                aliases = matching_topic.setdefault("aliases", [])
                if isinstance(aliases, list):
                    for alias in (raw_exact_topic, exact_topic):
                        if alias not in aliases:
                            aliases.append(alias)
            else:
                normalized_topic = normalize_topic_text(exact_topic)
                topic_id = upsert_topic(
                    connection,
                    exact_topic=exact_topic,
                    normalized_topic=normalized_topic,
                    representative_headline=raw_exact_topic,
                )
                new_topic = {
                    "id": topic_id,
                    "exact_topic": exact_topic,
                    "normalized_topic": normalized_topic,
                    "representative_headline": raw_exact_topic,
                    "aliases": list(dict.fromkeys([raw_exact_topic, exact_topic])),
                }
                existing_topics.append(new_topic)

            link_topic_source(
                connection,
                topic_id=topic_id,
                source_id=int(article["source_id"]),
                article_id=int(article["id"]),
                coverage_url=str(article["article_url"]),
            )
            topics_created_or_matched += 1
            linked_coverages += 1
            if article_index % 250 == 0:
                log_run_event(
                    connection,
                    run_id,
                    "phase_4_topic_extraction",
                    "progress",
                    f"Topic extraction progress: {article_index}/{len(articles)} article(s) checked.",
                )
                connection.commit()

        summary = TopicExtractionSummary(
            run_id=run_id,
            articles_checked=len(articles),
            topics_created_or_matched=topics_created_or_matched,
            linked_coverages=linked_coverages,
            skipped_articles=skipped_articles,
        )
        log_run_event(
            connection,
            run_id,
            "phase_4_topic_extraction",
            "complete",
            f"Topic extraction complete: {linked_coverages} coverage link(s), {skipped_articles} skipped.",
            json.dumps(summary.__dict__),
        )
        connection.commit()
        return summary


def extract_exact_topic_from_article(article: dict[str, object]) -> str | None:
    headline = clean_headline(article.get("headline"))
    if headline:
        return headline
    description = clean_headline(article.get("meta_description"))
    if description:
        return description
    article_text = str(article.get("article_text") or "")
    first_sentence = re.split(r"(?<=[.!?])\s+", article_text.strip(), maxsplit=1)[0]
    return clean_headline(first_sentence)


def clean_headline(value: object) -> str | None:
    if value is None:
        return None
    headline = str(value).strip()
    if not headline:
        return None
    headline = re.sub(r"\s+", " ", headline)
    headline = re.sub(r"\s+•\s+Ads of the World.*$", "", headline, flags=re.IGNORECASE)
    headline = re.sub(r"\s+LEARN MORE\s*>?.*$", "", headline, flags=re.IGNORECASE)
    headline = re.sub(r"\s+\|\s+[^|]+$", "", headline)
    headline = re.sub(r"\s+-\s+[^-]{2,40}$", "", headline)
    return headline if len(headline.split()) >= 4 else None


def canonical_topic_title(topic: str) -> str | None:
    event_key = named_event_key(topic)
    if event_key == "cannes lions 2026":
        return "Cannes Lions 2026 schedule, events and industry coverage"
    return None


def is_valid_exact_topic(
    topic: str,
    article: dict[str, object] | None = None,
    *,
    include_demo_articles: bool = False,
) -> bool:
    if is_generic_headline(topic):
        return False
    lowered = topic.lower()
    blocked_phrases = (
        "out of the box",
        "ads of the week",
        "campaigns that caught our eye",
        "cannes film festival",
        "deep dive",
        "homepage",
        "marketing whitepapers",
        "marketing news, voices and jobs",
        "find the latest",
        "leading source",
        "learn more",
        "part of the clio network",
        "ads of the world",
        "privacy policy",
        "terms of use",
    )
    if any(phrase in lowered for phrase in blocked_phrases):
        return False
    words = re.findall(r"[A-Za-z0-9]+", topic)
    if len(words) < 5:
        return False
    if len(words) > 36:
        return False
    has_signal = bool(
        re.search(
            r"\b(launch|launches|launched|unveil|unveils|appoint|appoints|sign|signs|wins|partners|debuts|rolls out|introduces|announces|campaign|film|ad|advertiser|brand|agency|platform|report|review|reviews|pits|pitch|pitches|hands|account|schedule|events|event|guide|calendar|festival|awards|things)\b",
            lowered,
        )
    )
    if not has_signal:
        return False
    if article:
        if is_demo_article(article) and not include_demo_articles:
            return False
        article_url = str(article.get("article_url") or "")
        source_url = str(article.get("source_url") or "")
        if article_url and not is_likely_article_url(article_url, topic, source_url=source_url):
            return False
    return True


def is_demo_article(article: dict[str, object]) -> bool:
    article_url = str(article.get("article_url") or "").lower()
    fetch_run_id = str(article.get("fetch_run_id") or "").lower()
    return "/demo/" in article_url or fetch_run_id.startswith("demo_")
