from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
import re

from src.config import DATABASE_PATH
from src.db import (
    get_connection,
    get_latest_locked_run_id,
    get_locked_topic_context,
    list_locked_topics,
    log_run_event,
    upsert_research_dossier,
)
import src.perplexity_research as perplexity_research
from src.settings_engine import get_research_engine_by_key, load_research_settings
from src.topic_matcher import is_same_news_event, topic_similarity


@dataclass(frozen=True)
class ResearchSummary:
    run_id: str
    dossiers_created: int
    locked_topics_checked: int


def build_research_dossiers(
    database_path=DATABASE_PATH,
    run_id: str | None = None,
) -> ResearchSummary:
    apply_saved_research_provider_settings(database_path=database_path)
    requested_run_id = run_id
    run_id = requested_run_id or datetime.now(UTC).strftime("run_%Y%m%d_%H%M%S")
    dossiers_created = 0

    with closing(get_connection(database_path)) as connection:
        locked_run_id = requested_run_id or get_latest_locked_run_id(connection)
        locked_topics = (
            list_locked_topics(connection, run_id=locked_run_id)
            if locked_run_id
            else []
        )
        for locked_topic in locked_topics:
            context = get_locked_topic_context(connection, int(locked_topic["id"]))
            dossier = build_local_dossier(context)
            if perplexity_research.ACTIVE_RESEARCH_PROVIDER == "approved_source_only":
                research_payload = build_approved_source_only_research_payload(
                    str(dossier.get("topic") or ""),
                    list(dossier.get("source_articles") or []),
                )
            else:
                research_payload = perplexity_research.research_topic_with_perplexity(
                    str(dossier.get("topic") or ""),
                    list(dossier.get("source_articles") or []),
                )
            dossier["perplexity_research"] = research_payload
            if research_payload.get("status") == "completed":
                dossier["research_mode"] = "perplexity_augmented"
            source_urls = dossier["source_urls"]
            upsert_research_dossier(
                connection,
                locked_topic_id=int(locked_topic["id"]),
                dossier_json=json.dumps(dossier, indent=2),
                source_urls_json=json.dumps(source_urls, indent=2),
            )
            dossiers_created += 1

        summary = ResearchSummary(
            run_id=run_id,
            dossiers_created=dossiers_created,
            locked_topics_checked=len(locked_topics),
        )
        log_run_event(
            connection,
            run_id,
            "phase_7_research",
            "complete",
            f"Built {dossiers_created} research dossier(s).",
            json.dumps(summary.__dict__),
        )
        connection.commit()
        return summary


def apply_saved_research_provider_settings(database_path=DATABASE_PATH) -> None:
    settings = load_research_settings(database_path=database_path)
    engine = get_research_engine_by_key(settings.get("provider_key"))
    api_key = str(settings.get("api_key") or "").strip()
    if not api_key and engine.get("env_var"):
        api_key = os.getenv(engine["env_var"], "").strip()
    perplexity_research.ACTIVE_RESEARCH_PROVIDER = engine["key"]
    perplexity_research.PERPLEXITY_API_KEY = api_key if engine["key"] == "perplexity" else ""
    perplexity_research.PERPLEXITY_MODEL = str(settings.get("model") or engine["model"]).strip() or engine["model"]
    perplexity_research.PERPLEXITY_ENDPOINT = (
        str(settings.get("endpoint") or engine["endpoint"]).strip() or engine["endpoint"]
    )


def build_approved_source_only_research_payload(
    topic: str,
    source_articles: list[dict[str, object]],
) -> dict[str, object]:
    source_names = sorted({str(article.get("source_name") or "Approved source") for article in source_articles})
    source_links = sorted({str(article["article_url"]) for article in source_articles if article.get("article_url")})
    evidence = ", ".join(source_names) if source_names else "no approved sources"
    return {
        "status": "approved_source_only",
        "topic": topic,
        "model": "local-approved-source-synthesis",
        "summary": (
            "External AI research is disabled. The content engine is using only exact-topic "
            f"approved-source coverage. Evidence base: {len(source_names)} source(s): {evidence}."
        ),
        "background_context": "data unavailable",
        "market_context": "data unavailable",
        "consumer_shift": "data unavailable",
        "brand_strategy": "data unavailable",
        "competitor_context": "data unavailable",
        "business_impact": "data unavailable",
        "cultural_relevance": "data unavailable",
        "marketing_lesson": "data unavailable",
        "verified_data_points": [],
        "source_links": source_links,
    }


def build_local_dossier(context: dict[str, object]) -> dict[str, object]:
    locked_topic = dict(context["locked_topic"])
    articles = list(context["articles"])
    topic_text = str(locked_topic.get("locked_topic") or locked_topic.get("exact_topic") or "")
    articles = select_topic_specific_articles(topic_text, articles)
    source_urls = sorted({str(article["article_url"]) for article in articles if article.get("article_url")})
    source_names = sorted({str(article["source_name"]) for article in articles if article.get("source_name")})
    source_articles = [
        {
            "source_name": article.get("source_name"),
            "headline": article.get("headline"),
            "article_url": article.get("article_url"),
            "meta_description": article.get("meta_description"),
            "published_date": article.get("published_date"),
        }
        for article in articles
        if article.get("article_url")
    ]
    combined_text = "\n\n".join(
        str(article.get("article_text") or article.get("meta_description") or article.get("headline") or "")
        for article in articles
    )

    return {
        "topic": locked_topic["locked_topic"],
        "brand_details": infer_brand_details(str(locked_topic["locked_topic"])),
        "campaign_name": infer_campaign_name(str(locked_topic["locked_topic"])),
        "agency_involved": find_phrase(combined_text, ["agency", "created by", "developed by"]),
        "campaign_objective": summarize_from_text(combined_text, "objective"),
        "creative_idea": summarize_from_text(combined_text, "creative idea"),
        "strategic_insight": summarize_from_text(combined_text, "strategic insight"),
        "execution": summarize_from_text(combined_text, "execution"),
        "media_platform_usage": find_media_platforms(combined_text),
        "audience": summarize_from_text(combined_text, "audience"),
        "market_context": summarize_from_text(combined_text, "market context"),
        "performance_data": find_performance_data(combined_text),
        "official_references": [],
        "supporting_references": source_urls,
        "source_names": source_names,
        "source_urls": source_urls,
        "source_articles": source_articles,
        "research_mode": "local_cited_synthesis",
    }


def select_topic_specific_articles(topic: str, articles: list[dict[str, object]]) -> list[dict[str, object]]:
    selected = []
    for article in articles:
        if is_topic_specific_article(topic, article):
            selected.append(article)
    return selected


def is_topic_specific_article(topic: str, article: dict[str, object]) -> bool:
    headline = str(article.get("headline") or "")
    meta_description = str(article.get("meta_description") or "")
    article_url = str(article.get("article_url") or "")
    slug_text = url_slug_text(article_url)
    candidate_text = " ".join(part for part in [headline, meta_description, slug_text] if part)
    if not topic or not candidate_text:
        return False
    if headline and is_same_news_event(topic, headline):
        return True
    if headline and topic_similarity(topic, headline) >= 0.62:
        return True
    return topic_similarity(topic, candidate_text) >= 0.68


def url_slug_text(article_url: str) -> str:
    cleaned = re.sub(r"^https?://", "", article_url.lower())
    cleaned = re.sub(r"[?#].*$", "", cleaned)
    parts = cleaned.split("/")
    slug = " ".join(parts[1:]) if len(parts) > 1 else cleaned
    slug = re.sub(r"\.[a-z0-9]+$", "", slug)
    slug = re.sub(r"[-_+/]+", " ", slug)
    slug = re.sub(r"\b[a-z0-9]{12,}\b", " ", slug)
    return re.sub(r"\s+", " ", slug).strip()


def infer_brand_details(topic: str) -> str:
    words = re.findall(r"[A-Z][A-Za-z0-9&'.-]+", topic)
    return words[0] if words else "To be confirmed from source coverage"


def infer_campaign_name(topic: str) -> str:
    quoted = re.findall(r"['\"]([^'\"]+)['\"]", topic)
    if quoted:
        return quoted[0]
    return "To be confirmed from source coverage"


def summarize_from_text(text: str, label: str) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return f"{label.title()} not available in stored article text."
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    return sentences[0][:600] if sentences else cleaned[:600]


def find_phrase(text: str, markers: list[str]) -> str:
    lowered = text.lower()
    for marker in markers:
        index = lowered.find(marker)
        if index >= 0:
            return text[max(0, index - 80) : index + 180].strip()
    return "Not confirmed in stored source coverage."


def find_media_platforms(text: str) -> list[str]:
    platforms = []
    for platform in ["TV", "YouTube", "Instagram", "TikTok", "Facebook", "LinkedIn", "OOH", "print", "retail", "social"]:
        if re.search(rf"\b{re.escape(platform)}\b", text, flags=re.IGNORECASE):
            platforms.append(platform)
    return platforms


def find_performance_data(text: str) -> str:
    matches = re.findall(
        r"[^.?!]*(?:\d+%|\d+\s?(?:million|billion|mn|bn)|views|impressions|sales)[^.?!]*[.?!]?",
        text,
        flags=re.IGNORECASE,
    )
    return " ".join(match.strip() for match in matches[:3]) or "No performance data found in stored source coverage."


def build_research_dossier() -> None:
    raise NotImplementedError("Use build_research_dossiers.")
