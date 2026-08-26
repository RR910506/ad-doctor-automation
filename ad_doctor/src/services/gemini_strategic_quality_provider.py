from __future__ import annotations

import json

from src.adapters.gemini_research_provider import GEMINI_MODEL
from src.domain.errors import DomainValidationError
from src.security.secret_provider import MissingSecretError, valid_secret


class GeminiStrategicQualityProvider:
    """Evidence-bound Gemini quality critic. Google Search is deliberately absent."""

    provider_name = "gemini"

    def __init__(self, *, api_key: str, model: str = GEMINI_MODEL, client: object | None = None) -> None:
        if not valid_secret(api_key):
            raise MissingSecretError("GEMINI_API_KEY is not configured")
        self.model_name = model.strip() or GEMINI_MODEL
        if client is None:
            try:
                from google import genai
            except ImportError as exc:
                raise RuntimeError("Google Gen AI SDK is not installed") from exc
            client = genai.Client(api_key=api_key)
        self.client = client

    def audit(self, *, package, evidence, deterministic_findings=()):
        prompt = f"""You are the Ad Doctor strategic quality auditor.
Do not browse, search, or add external facts. Evaluate only the validated Evidence Pack, Content Package,
and deterministic findings below. Assess strategic depth, Diagnosis, Doctor's Opinion, Prescription,
unsupported claims, fact/inference/opinion separation, generic or PR language, repetition, carousel quality,
and evidence discipline. Do not override deterministic blockers.
Return JSON only with dimension_scores (0-100), findings, reviewer_summary, and usage_notes.
Each finding must have severity, dimension, section, explanation, recommended_correction, blocking,
optional slide_number, and claim_ids. Only identify claim IDs present in the package.

INPUT JSON:
{json.dumps({'validated_evidence_pack': evidence, 'content_package': package, 'deterministic_findings': deterministic_findings}, ensure_ascii=False, default=str)}"""
        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config={"response_mime_type": "application/json", "temperature": 0.1},
            )
        except Exception as exc:
            raise RuntimeError(f"Gemini strategic quality audit failed ({type(exc).__name__})") from None
        try:
            result = json.loads(str(getattr(response, "text", "") or ""))
        except (ValueError, TypeError) as exc:
            raise DomainValidationError(f"Malformed strategic quality output: {type(exc).__name__}") from exc
        if not isinstance(result, dict):
            raise DomainValidationError("Strategic quality output must be a JSON object")
        result["usage"] = self._usage(getattr(response, "usage_metadata", None))
        return result

    @staticmethod
    def _usage(value: object | None) -> dict[str, int]:
        result = {}
        for name in ("prompt_token_count", "candidates_token_count", "total_token_count", "thoughts_token_count"):
            count = value.get(name) if isinstance(value, dict) else getattr(value, name, None)
            if count is not None:
                result[name] = int(count)
        return result
