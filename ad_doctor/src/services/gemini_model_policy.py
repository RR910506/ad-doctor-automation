from __future__ import annotations

from dataclasses import dataclass
import re

from src.adapters.gemini_research_provider import GeminiProviderError, classify_gemini_error


PRIMARY_GEMINI_MODEL = "gemini-3.7-flash"
AVAILABILITY_FALLBACK_MODEL = "gemini-3.5-flash"


@dataclass(frozen=True, slots=True)
class GeminiCallResult:
    response: object
    model: str
    attempts: int


def select_production_flash_model(model_names: list[str]) -> str | None:
    clean = [name.removeprefix("models/") for name in model_names]
    stable = [name for name in clean if "flash" in name and not any(marker in name.lower() for marker in ("preview", "exp", "image", "tts", "audio", "omni", "lite"))]
    for preferred in (PRIMARY_GEMINI_MODEL, "gemini-3.6-flash"):
        if preferred in stable:
            return preferred
    def version(name: str) -> tuple[int, ...]:
        match = re.search(r"gemini-(\d+(?:\.\d+)*)-flash", name)
        return tuple(int(part) for part in match.group(1).split(".")) if match else ()
    return max(stable, key=lambda name: (version(name), name)) if stable else None


def generate_with_availability_fallback(
    client: object,
    *,
    contents: object,
    config: object | None,
    primary_model: str = PRIMARY_GEMINI_MODEL,
    fallback_model: str = AVAILABILITY_FALLBACK_MODEL,
) -> GeminiCallResult:
    """Use one fallback only when the primary fails with 503 UNAVAILABLE."""
    try:
        response = client.models.generate_content(model=primary_model, contents=contents, config=config)
        return GeminiCallResult(response, primary_model, 1)
    except Exception as exc:
        diagnostic = classify_gemini_error(exc)
        if not (diagnostic.code == 503 and diagnostic.category == "provider_unavailable"):
            raise GeminiProviderError(diagnostic) from None
    try:
        response = client.models.generate_content(model=fallback_model, contents=contents, config=config)
        return GeminiCallResult(response, fallback_model, 2)
    except Exception as exc:
        raise GeminiProviderError(classify_gemini_error(exc)) from None
