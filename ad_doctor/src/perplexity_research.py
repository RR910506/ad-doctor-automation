from __future__ import annotations

import json
import re
from typing import Any

import requests

from src.config import PERPLEXITY_API_KEY, PERPLEXITY_ENDPOINT, PERPLEXITY_MODEL


RESEARCH_KEYS = (
    "summary",
    "background_context",
    "market_context",
    "consumer_shift",
    "brand_strategy",
    "competitor_context",
    "business_impact",
    "cultural_relevance",
    "marketing_lesson",
    "verified_data_points",
    "source_links",
)


def research_topic_with_perplexity(
    topic: str,
    source_articles: list[dict[str, object]],
) -> dict[str, Any]:
    source_articles = prepare_source_articles(source_articles)
    if not PERPLEXITY_API_KEY:
        return build_unavailable_research(topic, source_articles)

    payload = {
        "model": PERPLEXITY_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a marketing intelligence researcher for The Ad Doctor. "
                    "Research only the exact topic provided. Do not drift into unrelated "
                    "stories about the same brand, agency, or platform. Return strict JSON "
                    "with these keys: summary, background_context, market_context, "
                    "consumer_shift, brand_strategy, competitor_context, business_impact, "
                    "cultural_relevance, marketing_lesson, verified_data_points, source_links. "
                    "Every number must include a source URL. If data is unavailable, write "
                    "\"data unavailable\". Separate facts from interpretation."
                ),
            },
            {
                "role": "user",
                "content": build_research_prompt(topic, source_articles),
            },
        ],
        "temperature": 0.2,
    }
    try:
        response = requests.post(
            PERPLEXITY_ENDPOINT,
            headers={
                "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=45,
        )
        response.raise_for_status()
        response_json = response.json()
    except requests.RequestException as exc:
        return build_error_research(topic, source_articles, exc)
    except ValueError as exc:
        return build_error_research(topic, source_articles, exc)

    content = extract_response_content(response_json)
    parsed = parse_json_object(content)
    citations = extract_citations(response_json)
    return normalize_research_result(
        topic,
        source_articles,
        parsed,
        content,
        citations,
        response_json.get("usage"),
    )


def prepare_source_articles(source_articles: list[dict[str, object]]) -> list[dict[str, object]]:
    prepared = []
    seen_urls = set()
    for article in source_articles:
        url = str(article.get("article_url") or "").strip()
        if not url or url.lower() in seen_urls:
            continue
        seen_urls.add(url.lower())
        prepared.append(
            {
                "source_name": str(article.get("source_name") or "Approved source").strip(),
                "headline": str(article.get("headline") or "").strip(),
                "article_url": url,
                "published_date": str(article.get("published_date") or "").strip(),
                "meta_description": str(article.get("meta_description") or "").strip(),
            }
        )
    return prepared[:10]


def build_research_prompt(topic: str, source_articles: list[dict[str, object]]) -> str:
    source_lines = []
    for index, article in enumerate(source_articles, start=1):
        source_lines.append(
            "\n".join(
                [
                    f"{index}. Source: {article.get('source_name') or 'Approved source'}",
                    f"Headline: {article.get('headline') or 'headline unavailable'}",
                    f"URL: {article.get('article_url')}",
                    f"Published date: {article.get('published_date') or 'date unavailable'}",
                    f"Description: {article.get('meta_description') or 'description unavailable'}",
                ]
            )
        )
    return (
        f"Exact topic to research: {topic}\n\n"
        "Approved same-topic source articles:\n"
        f"{chr(10).join(source_lines) if source_lines else 'No approved source links supplied.'}\n\n"
        "Research requirements:\n"
        "- Use only the exact topic and directly relevant supporting context.\n"
        "- Exclude unrelated articles about the same brand or agency.\n"
        "- Include verified data points only when a source URL supports them.\n"
        "- Return JSON only."
    )


def extract_response_content(response_json: dict[str, Any]) -> str:
    choices = response_json.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else {}
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                return content.strip()
    content = response_json.get("content")
    return str(content or "").strip()


def parse_json_object(content: str) -> dict[str, Any]:
    if not content:
        return {}
    cleaned = content.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if not match:
        return {}
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def extract_citations(response_json: dict[str, Any]) -> list[str]:
    citations = response_json.get("citations") or response_json.get("source_links") or []
    links = []
    if isinstance(citations, list):
        for citation in citations:
            if isinstance(citation, str):
                links.append(citation)
            elif isinstance(citation, dict):
                url = citation.get("url") or citation.get("link")
                if url:
                    links.append(str(url))
    search_results = response_json.get("search_results")
    if isinstance(search_results, list):
        for result in search_results:
            if isinstance(result, dict) and result.get("url"):
                links.append(str(result["url"]))
    return dedupe_links(links)


def normalize_research_result(
    topic: str,
    source_articles: list[dict[str, object]],
    parsed: dict[str, Any],
    content: str,
    citations: list[str],
    usage: Any = None,
) -> dict[str, Any]:
    result = {key: normalize_value(parsed.get(key)) for key in RESEARCH_KEYS}
    if not result["summary"]:
        result["summary"] = (
            normalize_whitespace(content)[:1200]
            if content
            else "Perplexity returned no usable summary; using stored approved-source synthesis only."
        )
    result["verified_data_points"] = normalize_data_points(parsed.get("verified_data_points"))
    source_links = normalize_links(parsed.get("source_links"))
    source_links.extend(citations)
    source_links.extend(str(article["article_url"]) for article in source_articles if article.get("article_url"))
    result["source_links"] = dedupe_links(source_links)
    result["status"] = "completed"
    result["topic"] = topic
    result["model"] = PERPLEXITY_MODEL
    if isinstance(usage, dict):
        result["usage"] = usage
    return result


def build_unavailable_research(topic: str, source_articles: list[dict[str, object]]) -> dict[str, Any]:
    source_names = sorted({str(article.get("source_name") or "Approved source") for article in source_articles})
    source_links = [str(article["article_url"]) for article in source_articles if article.get("article_url")]
    summary = (
        "Perplexity API key not configured; using local exact-topic approved-source synthesis only. "
        f"Evidence base: {len(source_names)} approved source(s): {', '.join(source_names) if source_names else 'none'}."
    )
    return fallback_research_payload(
        topic=topic,
        status="unavailable",
        summary=summary,
        source_links=source_links,
    )


def build_error_research(
    topic: str,
    source_articles: list[dict[str, object]],
    exc: Exception,
) -> dict[str, Any]:
    source_links = [str(article["article_url"]) for article in source_articles if article.get("article_url")]
    return fallback_research_payload(
        topic=topic,
        status="error",
        summary=(
            "Perplexity research failed, so the draft uses local exact-topic approved-source synthesis only. "
            f"Error: {type(exc).__name__}: {exc}"
        ),
        source_links=source_links,
    )


def fallback_research_payload(
    *,
    topic: str,
    status: str,
    summary: str,
    source_links: list[str],
) -> dict[str, Any]:
    return {
        "status": status,
        "topic": topic,
        "model": PERPLEXITY_MODEL,
        "summary": summary,
        "background_context": "data unavailable",
        "market_context": "data unavailable",
        "consumer_shift": "data unavailable",
        "brand_strategy": "data unavailable",
        "competitor_context": "data unavailable",
        "business_impact": "data unavailable",
        "cultural_relevance": "data unavailable",
        "marketing_lesson": "data unavailable",
        "verified_data_points": [],
        "source_links": dedupe_links(source_links),
    }


def normalize_value(value: Any) -> str:
    if isinstance(value, str):
        return normalize_whitespace(value)
    if isinstance(value, list):
        return normalize_whitespace("; ".join(str(item) for item in value if str(item).strip()))
    if isinstance(value, dict):
        return normalize_whitespace(json.dumps(value, ensure_ascii=True))
    return normalize_whitespace(str(value or ""))


def normalize_data_points(value: Any) -> list[dict[str, str]]:
    if not value:
        return []
    if isinstance(value, str):
        return [{"claim": normalize_whitespace(value), "source": "source unavailable"}]
    if not isinstance(value, list):
        return [{"claim": normalize_value(value), "source": "source unavailable"}]
    points = []
    for item in value:
        if isinstance(item, dict):
            claim = normalize_value(item.get("claim") or item.get("data") or item.get("point"))
            source = normalize_value(item.get("source") or item.get("url"))
        else:
            claim = normalize_value(item)
            source = "source unavailable"
        if claim and claim.lower() != "data unavailable":
            points.append({"claim": claim, "source": source or "source unavailable"})
    return points


def normalize_links(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.startswith(("http://", "https://")) else []
    if not isinstance(value, list):
        return []
    links = []
    for item in value:
        if isinstance(item, str):
            links.append(item)
        elif isinstance(item, dict):
            url = item.get("url") or item.get("source")
            if url:
                links.append(str(url))
    return dedupe_links(links)


def dedupe_links(links: list[str]) -> list[str]:
    deduped = []
    seen = set()
    for link in links:
        clean_link = str(link).strip()
        if not clean_link.startswith(("http://", "https://")):
            continue
        key = clean_link.lower().rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        deduped.append(clean_link)
    return deduped


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
