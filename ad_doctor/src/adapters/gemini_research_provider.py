from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urldefrag

from src.security.secret_provider import MissingSecretError, valid_secret


GEMINI_MODEL = "gemini-2.5-flash"


@runtime_checkable
class ResearchProvider(Protocol):
    provider_name: str
    model: str

    def research(self, *, topic: str, source_articles: list[dict[str, object]], research_brief: str) -> "NormalizedResearchResult": ...


@dataclass(frozen=True, slots=True)
class GroundedSource:
    url: str
    title: str


@dataclass(frozen=True, slots=True)
class NormalizedResearchResult:
    provider: str
    model: str
    final_text: str
    structured: dict[str, object]
    grounded_sources: tuple[GroundedSource, ...]
    grounding_metadata: dict[str, object]
    usage: dict[str, int]


class GeminiResearchProvider:
    """Official Gemini client adapter with mandatory Google Search grounding."""

    provider_name = "gemini"

    def __init__(self, *, api_key: str, model: str = GEMINI_MODEL, client: object | None = None) -> None:
        if not valid_secret(api_key):
            raise MissingSecretError("GEMINI_API_KEY is not configured")
        self.model = model.strip() or GEMINI_MODEL
        if client is None:
            try:
                from google import genai
            except ImportError as exc:
                raise RuntimeError("Google Gen AI SDK is not installed") from exc
            client = genai.Client(api_key=api_key)
        self._client = client

    def research(self, *, topic: str, source_articles: list[dict[str, object]], research_brief: str) -> NormalizedResearchResult:
        prompt = _build_prompt(topic, source_articles, research_brief)
        try:
            response = self._client.models.generate_content(
                model=self.model,
                contents=prompt,
                config={
                    "tools": [{"google_search": {}}],
                    "response_mime_type": "application/json",
                    "temperature": 0.1,
                },
            )
        except Exception as exc:
            # Never include provider messages: some HTTP clients echo request headers.
            raise RuntimeError(f"Gemini grounded research failed ({type(exc).__name__})") from None
        text = str(getattr(response, "text", "") or "").strip()
        structured = _parse_json(text)
        metadata = _first_grounding_metadata(response)
        sources = _grounded_sources(metadata, structured)
        usage = _usage(getattr(response, "usage_metadata", None))
        return NormalizedResearchResult(
            provider=self.provider_name,
            model=self.model,
            final_text=text,
            structured=structured,
            grounded_sources=sources,
            grounding_metadata=_safe_metadata(metadata),
            usage=usage,
        )


def _build_prompt(topic: str, source_articles: list[dict[str, object]], research_brief: str) -> str:
    seeds = [{"publication": item.get("source_name"), "title": item.get("headline"), "url": item.get("article_url")} for item in source_articles]
    return f"""You are the research stage for Ad Doctor, a marketing intelligence and opinion platform.
Use Google Search extensively. Do not rely on ungrounded model knowledge for recent claims.
Research topic: {topic}

Research direction:
{research_brief}

Qualifying coverage seeds (not sufficient as the whole investigation):
{json.dumps(seeds, ensure_ascii=False)}

Return one JSON object only with these fields:
case_summary (string), event_facts, brand_context, agency_context, consumer_context,
category_context, cultural_context, competitor_context, historical_context,
confirmed_results, strategic_signals, contradictions, unknowns, source_catalog.
Every item except case_summary and source_catalog must be an object with statement,
source_urls (array of exact URLs), confidence (0..1), and notes. Facts and confirmed
results without a supporting URL must go under unknowns, never event_facts.
Strategic signals are hypotheses/inferences, not verified facts. Do not state motives
or outcomes as facts unless a source explicitly supports them. Prefer official company
and agency sources, then reputable marketing/business publications and industry research.
Each source_catalog item must contain url, title, publication, published_at, source_type,
and credibility_tier. Do not invent a campaign name."""


def _parse_json(text: str) -> dict[str, object]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError("Gemini returned invalid structured research JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("Gemini structured research must be a JSON object")
    return value


def _first_grounding_metadata(response: object) -> object | None:
    candidates = getattr(response, "candidates", None) or []
    return getattr(candidates[0], "grounding_metadata", None) if candidates else None


def _read(value: object, name: str, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def _canonical_url(value: object) -> str:
    url = urldefrag(str(value or "").strip())[0]
    return url.rstrip("/")


def _grounded_sources(metadata: object | None, structured: dict[str, object]) -> tuple[GroundedSource, ...]:
    found: dict[str, GroundedSource] = {}
    for chunk in _read(metadata, "grounding_chunks", []) or []:
        web = _read(chunk, "web")
        url = _canonical_url(_read(web, "uri"))
        if url:
            found.setdefault(url, GroundedSource(url, str(_read(web, "title", "Grounded web source") or "Grounded web source")))
    for item in structured.get("source_catalog", []) if isinstance(structured.get("source_catalog"), list) else []:
        if not isinstance(item, dict):
            continue
        url = _canonical_url(item.get("url"))
        if url:
            found.setdefault(url, GroundedSource(url, str(item.get("title") or "Grounded web source")))
    return tuple(found.values())


def _safe_metadata(metadata: object | None) -> dict[str, object]:
    supports = []
    for support in _read(metadata, "grounding_supports", []) or []:
        segment = _read(support, "segment")
        supports.append({"text": str(_read(segment, "text", "") or ""), "grounding_chunk_indices": list(_read(support, "grounding_chunk_indices", []) or [])})
    return {"web_search_queries": list(_read(metadata, "web_search_queries", []) or []), "grounding_supports": supports}


def _usage(value: object | None) -> dict[str, int]:
    names = ("prompt_token_count", "candidates_token_count", "total_token_count", "thoughts_token_count")
    return {name: int(_read(value, name, 0) or 0) for name in names if int(_read(value, name, 0) or 0)}
