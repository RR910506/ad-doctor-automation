from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.adapters.gemini_research_provider import GEMINI_MODEL, GeminiResearchProvider
from src.config import DATABASE_PATH
from src.security.resolver import build_secret_provider
from src.security.secret_provider import MissingSecretError
from src.services.gemini_strategic_quality_provider import GeminiStrategicQualityProvider
from src.services.structured_gemini_content_provider import StructuredGeminiContentProvider


@dataclass(frozen=True, slots=True)
class GeminiProductionProviders:
    research: GeminiResearchProvider
    content: StructuredGeminiContentProvider
    quality: GeminiStrategicQualityProvider


def build_gemini_production_providers(
    database_path: Path = DATABASE_PATH,
    *,
    secret_provider=None,
    client: object | None = None,
    model: str = GEMINI_MODEL,
) -> GeminiProductionProviders:
    """Resolve the only required AI credential and share one injected Gemini client."""
    secret = (secret_provider or build_secret_provider(database_path)).resolve("GEMINI_API_KEY")
    if not secret.configured or not secret.value:
        raise MissingSecretError("GEMINI_API_KEY is not configured")
    if client is None:
        try:
            from google import genai
        except ImportError as exc:
            raise RuntimeError("Google Gen AI SDK is not installed") from exc
        client = genai.Client(api_key=secret.value)
    return GeminiProductionProviders(
        research=GeminiResearchProvider(api_key=secret.value, model=model, client=client),
        content=StructuredGeminiContentProvider(api_key=secret.value, model=model, client=client),
        quality=GeminiStrategicQualityProvider(api_key=secret.value, model=model, client=client),
    )
