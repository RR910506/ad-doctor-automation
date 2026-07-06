from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Any

from src.config import DATABASE_PATH
from src.settings_engine import load_content_writer_settings


DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"
DEFAULT_OPENAI_ENDPOINT = "https://api.openai.com/v1"


@dataclass(frozen=True)
class WriterResult:
    status: str
    draft_markdown: str
    provider: str
    model: str
    usage: dict[str, int]
    error_message: str | None = None


def load_writer_runtime_settings(database_path=DATABASE_PATH) -> dict[str, str]:
    saved = load_content_writer_settings(database_path=database_path)
    api_key = str(saved.get("api_key") or "").strip() or os.getenv("OPENAI_API_KEY", "").strip()
    return {
        "provider_key": str(saved.get("provider_key") or "openai").strip() or "openai",
        "enabled": str(saved.get("enabled") or "1"),
        "api_key": api_key,
        "model": str(saved.get("model") or os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)).strip()
        or DEFAULT_OPENAI_MODEL,
        "endpoint": str(saved.get("endpoint") or os.getenv("OPENAI_BASE_URL", DEFAULT_OPENAI_ENDPOINT)).strip()
        or DEFAULT_OPENAI_ENDPOINT,
    }


def is_openai_writer_enabled(database_path=DATABASE_PATH) -> bool:
    settings = load_writer_runtime_settings(database_path=database_path)
    return (
        settings["provider_key"] == "openai"
        and settings["enabled"] not in {"0", "false", "False", "off", "no"}
        and bool(settings["api_key"])
    )


def write_final_draft_with_chatgpt(
    *,
    content_type: str,
    local_draft: str,
    dossier: dict[str, object],
    dissection_markdown: str,
    framework_settings: dict[str, str],
    database_path=DATABASE_PATH,
) -> WriterResult:
    settings = load_writer_runtime_settings(database_path=database_path)
    if settings["provider_key"] != "openai":
        return WriterResult(
            status="disabled",
            draft_markdown=append_writer_disabled_note(
                local_draft,
                "Content writer provider is not OpenAI.",
            ),
            provider=settings["provider_key"],
            model=settings["model"],
            usage={},
            error_message="Content writer provider is not OpenAI.",
        )
    if settings["enabled"] in {"0", "false", "False", "off", "no"}:
        return WriterResult(
            status="disabled",
            draft_markdown=append_writer_disabled_note(local_draft, "ChatGPT content writing is disabled."),
            provider="openai",
            model=settings["model"],
            usage={},
            error_message="ChatGPT content writing is disabled.",
        )
    if not settings["api_key"]:
        return WriterResult(
            status="unavailable",
            draft_markdown=append_writer_disabled_note(local_draft, "OPENAI_API_KEY is missing."),
            provider="openai",
            model=settings["model"],
            usage={},
            error_message="OPENAI_API_KEY is missing.",
        )

    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings["api_key"], base_url=settings["endpoint"])
        response = client.chat.completions.create(
            model=settings["model"],
            temperature=0.35,
            messages=[
                {
                    "role": "system",
                    "content": build_writer_system_prompt(content_type),
                },
                {
                    "role": "user",
                    "content": build_writer_user_prompt(
                        content_type=content_type,
                        local_draft=local_draft,
                        dossier=dossier,
                        dissection_markdown=dissection_markdown,
                        framework_settings=framework_settings,
                    ),
                },
            ],
        )
        draft = extract_chat_completion_text(response)
        if not draft:
            raise ValueError("OpenAI returned an empty draft.")
        return WriterResult(
            status="completed",
            draft_markdown=append_writer_note(draft, settings["model"]),
            provider="openai",
            model=settings["model"],
            usage=extract_usage(response),
        )
    except Exception as exc:
        return WriterResult(
            status="error",
            draft_markdown=append_writer_fallback_note(local_draft, exc),
            provider="openai",
            model=settings["model"],
            usage={},
            error_message=f"{type(exc).__name__}: {exc}",
        )


def build_writer_system_prompt(content_type: str) -> str:
    if content_type == "social":
        target = "Instagram carousel draft"
    else:
        target = "blog draft"
    return (
        "You are ChatGPT acting as The Ad Doctor's final content writer. "
        f"Create a {target} from the provided evidence and framework. "
        "Do not scrape the web. Do not add facts, numbers, quotes, sources, or claims that are not provided. "
        "Separate article facts from Ad Doctor interpretation. "
        "If data is unclear, write data unavailable. "
        "Keep the tone strategic, simple, opinionated, and diagnostic."
    )


def build_writer_user_prompt(
    *,
    content_type: str,
    local_draft: str,
    dossier: dict[str, object],
    dissection_markdown: str,
    framework_settings: dict[str, str],
) -> str:
    if content_type == "social":
        instruction = (
            "Rewrite the local draft into the exact 8-slide Ad Doctor carousel structure. "
            "Each slide needs Headline, Body, Visual Suggestion, Bold Words, and Source. "
            "Keep source references topic-specific and do not add irrelevant links."
        )
    else:
        instruction = (
            "Rewrite the local draft into a clear Ad Doctor blog draft. "
            "Use diagnostic sections, cite only supplied source links, and end with a marketing prescription."
        )
    evidence = {
        "topic": dossier.get("topic"),
        "source_urls": dossier.get("source_urls"),
        "source_articles": dossier.get("source_articles"),
        "perplexity_research": dossier.get("perplexity_research"),
        "framework": framework_settings,
    }
    return "\n\n".join(
        [
            instruction,
            "Evidence JSON:",
            json.dumps(evidence, ensure_ascii=True, indent=2, default=str)[:18000],
            "Campaign dissection notes:",
            str(dissection_markdown or "")[:6000],
            "Local deterministic draft to improve:",
            local_draft[:14000],
        ]
    )


def extract_chat_completion_text(response: Any) -> str:
    choices = getattr(response, "choices", None)
    if choices:
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None)
        if isinstance(content, str):
            return content.strip()
    return ""


def extract_usage(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    result = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = getattr(usage, key, None)
        if value is not None:
            result[key] = int(value)
    return result


def append_writer_note(draft: str, model: str) -> str:
    return f"{draft.rstrip()}\n\n---\n\nContent Writing Mode: ChatGPT/OpenAI\nContent Writing Model: {model}\n"


def append_writer_fallback_note(local_draft: str, exc: Exception) -> str:
    return (
        f"{local_draft.rstrip()}\n\n---\n\n"
        "Content Writing Mode: Local framework fallback\n"
        f"ChatGPT Writing Error: {type(exc).__name__}: {exc}\n"
    )


def append_writer_disabled_note(local_draft: str, reason: str) -> str:
    return (
        f"{local_draft.rstrip()}\n\n---\n\n"
        "Content Writing Mode: Local framework generator\n"
        f"ChatGPT Writing Status: disabled ({reason})\n"
    )
