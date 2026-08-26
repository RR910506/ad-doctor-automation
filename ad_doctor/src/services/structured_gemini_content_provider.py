from __future__ import annotations

import json

from src.adapters.gemini_research_provider import GEMINI_MODEL
from src.domain.errors import DomainValidationError
from src.security.secret_provider import MissingSecretError, valid_secret


class StructuredGeminiContentProvider:
    """Gemini structured-content adapter. It never enables web-search tools."""

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
        self.last_usage: dict[str, int] = {}

    def generate(self, *, case, evidence, framework, configuration, prompts):
        response = self._call(
            {
                "task": "generate_ad_doctor_content_package",
                "case": case,
                "validated_evidence_pack": evidence,
                "framework": framework,
                "configuration": configuration,
                "prompt_contracts": prompts,
            },
            "Generate the complete existing ContentPackage JSON contract. Use only the validated Evidence Pack as factual input.",
        )
        result = self._parse(response)
        result["generation_provider"] = self.provider_name
        result["generation_model"] = self.model_name
        result.setdefault("metadata", {})["provider_usage"] = dict(self.last_usage)
        return result

    def revise(self, *, package, target, instruction, evidence):
        response = self._call(
            {
                "task": "revise_ad_doctor_content_package",
                "package": package,
                "target": target,
                "instruction": instruction,
                "validated_evidence_pack": evidence,
            },
            "Revise only the requested target. Preserve immutable versioning and all evidence traceability.",
        )
        result = self._parse(response)
        result["generation_provider"] = self.provider_name
        result["generation_model"] = self.model_name
        result.setdefault("metadata", {})["provider_usage"] = dict(self.last_usage)
        return result

    def _call(self, payload: dict[str, object], task_instruction: str):
        prompt = f"""You are the Ad Doctor structured content engine.
{task_instruction}
Google Search and external knowledge are forbidden. The validated Evidence Pack is the sole factual source.
Keep VERIFIED_FACT, SOURCE_CLAIM, STRATEGIC_INFERENCE, DOCTOR_OPINION, and UNKNOWN distinct.
Never promote an unsupported inference to fact. Every verified fact must reference valid evidence_item_ids and source_ids.
Strategic inferences must be qualified. Doctor's Opinion must be evidence-connected, independent, specific, and decisive.
Symptoms, Patient History, Diagnosis, Treatment, Results, Prescription, and Doctor's Opinion are mandatory.
Also include executive_diagnosis, business_problem, marketing_problem, supported consumer/category tension,
strategic_rationale, risks, missed_opportunities, alternative_treatment, final_verdict, carousel, social_caption,
article, and design_direction. Results must explicitly say evidence is unavailable when no verified result exists.
Return one JSON object only, matching the existing ContentPackage schema exactly.

INPUT JSON:
{json.dumps(payload, ensure_ascii=False, default=str)}"""
        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config={"response_mime_type": "application/json", "temperature": 0.2},
            )
        except Exception as exc:
            raise RuntimeError(f"Gemini content generation failed ({type(exc).__name__})") from None
        self.last_usage = self._usage(getattr(response, "usage_metadata", None))
        return response

    @staticmethod
    def _parse(response):
        value = getattr(response, "text", response)
        try:
            parsed = json.loads(value) if isinstance(value, str) else dict(value)
        except (ValueError, TypeError) as exc:
            raise DomainValidationError(f"Malformed structured provider output: {type(exc).__name__}") from exc
        if not isinstance(parsed, dict):
            raise DomainValidationError("Structured content output must be a JSON object")
        return parsed

    @staticmethod
    def _usage(value: object | None) -> dict[str, int]:
        result = {}
        for name in ("prompt_token_count", "candidates_token_count", "total_token_count", "thoughts_token_count"):
            count = value.get(name) if isinstance(value, dict) else getattr(value, name, None)
            if count is not None:
                result[name] = int(count)
        return result
