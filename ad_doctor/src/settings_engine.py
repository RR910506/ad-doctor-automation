from __future__ import annotations

from contextlib import closing
import json
import re
from typing import Any

from src.config import DATABASE_PATH
from src.db import get_connection


RESEARCH_ENGINE_OPTIONS: tuple[dict[str, str], ...] = (
    {
        "key": "browser",
        "label": "Browser Research Engine",
        "status": "HTTP-first approved-source research",
        "api_key_label": "No research API key needed",
        "model": "approved-source-browser",
        "endpoint": "",
        "env_var": "",
    },
    {
        "key": "gemini",
        "label": "Gemini Evidence Reasoning (optional)",
        "status": "Ungrounded reasoning over retrieved evidence",
        "api_key_label": "Gemini API key",
        "model": "gemini-3.7-flash",
        "endpoint": "https://generativelanguage.googleapis.com",
        "env_var": "GEMINI_API_KEY",
    },
    {
        "key": "perplexity",
        "label": "Perplexity",
        "status": "Live deep research",
        "api_key_label": "Perplexity API key",
        "model": "sonar-pro",
        "endpoint": "https://api.perplexity.ai/v1/sonar",
        "env_var": "PERPLEXITY_API_KEY",
    },
    {
        "key": "openai_web",
        "label": "OpenAI Web Search",
        "status": "Research-ready provider slot",
        "api_key_label": "OpenAI API key",
        "model": "gpt-4.1",
        "endpoint": "https://api.openai.com/v1/responses",
        "env_var": "OPENAI_API_KEY",
    },
    {
        "key": "tavily",
        "label": "Tavily Search",
        "status": "Search research provider slot",
        "api_key_label": "Tavily API key",
        "model": "tavily-search",
        "endpoint": "https://api.tavily.com/search",
        "env_var": "TAVILY_API_KEY",
    },
    {
        "key": "exa",
        "label": "Exa Search",
        "status": "Search research provider slot",
        "api_key_label": "Exa API key",
        "model": "exa-search",
        "endpoint": "https://api.exa.ai/search",
        "env_var": "EXA_API_KEY",
    },
    {
        "key": "brave",
        "label": "Brave Search",
        "status": "Search research provider slot",
        "api_key_label": "Brave Search API key",
        "model": "brave-web-search",
        "endpoint": "https://api.search.brave.com/res/v1/web/search",
        "env_var": "BRAVE_SEARCH_API_KEY",
    },
    {
        "key": "serpapi",
        "label": "SerpAPI / Google Search",
        "status": "Search research provider slot",
        "api_key_label": "SerpAPI key",
        "model": "google-search",
        "endpoint": "https://serpapi.com/search.json",
        "env_var": "SERPAPI_API_KEY",
    },
    {
        "key": "bing",
        "label": "Bing Web Search",
        "status": "Search research provider slot",
        "api_key_label": "Bing Search API key",
        "model": "bing-web-search",
        "endpoint": "https://api.bing.microsoft.com/v7.0/search",
        "env_var": "BING_SEARCH_API_KEY",
    },
    {
        "key": "newsapi",
        "label": "NewsAPI",
        "status": "News search provider slot",
        "api_key_label": "NewsAPI key",
        "model": "newsapi-everything",
        "endpoint": "https://newsapi.org/v2/everything",
        "env_var": "NEWSAPI_KEY",
    },
    {
        "key": "you",
        "label": "You.com Search",
        "status": "Research-ready provider slot",
        "api_key_label": "You.com API key",
        "model": "you-search",
        "endpoint": "https://api.ydc-index.io/search",
        "env_var": "YOU_API_KEY",
    },
    {
        "key": "approved_source_only",
        "label": "Approved Sources Only",
        "status": "No external API credits",
        "api_key_label": "No API key needed",
        "model": "local-approved-source-synthesis",
        "endpoint": "",
        "env_var": "",
    },
    {
        "key": "custom",
        "label": "Custom Research API",
        "status": "Custom endpoint",
        "api_key_label": "Custom API key",
        "model": "custom",
        "endpoint": "",
        "env_var": "CUSTOM_RESEARCH_API_KEY",
    },
)


DEFAULT_RESEARCH_SETTINGS = {
    "provider_key": "browser",
    "model": "approved-source-browser",
    "endpoint": "",
    "api_key": "",
}

DEFAULT_CONTENT_WRITER_SETTINGS = {
    "provider_key": "gemini",
    "enabled": "1",
    "model": "gemini-3.7-flash",
    "endpoint": "https://generativelanguage.googleapis.com",
    "api_key": "",
}


DEFAULT_CONTENT_FRAMEWORK = {
    "framework_name": "The Ad Doctor Diagnostic Carousel",
    "brand_worldview": (
        "The Ad Doctor does not report marketing news. It diagnoses why marketing events "
        "happen, what they reveal about people, culture, platforms, brands, agencies, and markets, "
        "and what marketers should learn."
    ),
    "qualification_rule": (
        "Create content only when the topic is covered by at least 3 independent approved publisher networks. "
        "If coverage is below 3, reject the topic and do not create a publishable draft."
    ),
    "content_pillars": json.dumps(
        [
            "Campaign Surgery",
            "Agency Diagnosis",
            "Platform Pulse",
            "Consumer Pulse",
            "Culture Scan",
            "Brand Checkup",
            "Doctor's Verdict",
            "Marketing Prescription",
        ],
        indent=2,
    ),
    "carousel_structure": json.dumps(
        [
            {"slide": 1, "role": "Hook", "purpose": "Sharp strategic headline plus a short business-focused intro."},
            {"slide": 2, "role": "Problem / Context", "purpose": "Explain what was broken, inaccessible, ignored, or misunderstood."},
            {"slide": 3, "role": "Strategic Move", "purpose": "Explain what the brand, agency, or platform changed."},
            {"slide": 4, "role": "Consumer Shift", "purpose": "Explain the audience behavior, perception, habit, or participation shift."},
            {"slide": 5, "role": "Brand / Market Impact", "purpose": "Explain relevance, attention, growth, sales, sponsorship, or category impact."},
            {"slide": 6, "role": "Why It Worked", "purpose": "Break down the strategic reason in simple language."},
            {"slide": 7, "role": "Result / Business Outcome", "purpose": "Use verified numbers only; write data unavailable when unsupported."},
            {"slide": 8, "role": "What We Think", "purpose": "Give Ad Doctor's opinionated verdict and marketer lesson."},
        ],
        indent=2,
    ),
    "tone_rules": (
        "Strategic, intelligent, curious, opinionated, educational, respectful, and simple. "
        "Avoid clickbait, press-release language, generic marketing jargon, fanboy tone, and unsupported claims."
    ),
    "psychology_lenses": json.dumps(
        [
            "Identity",
            "Belonging",
            "Status",
            "Validation",
            "Aspiration",
            "Trust",
            "Fear",
            "Curiosity",
            "Humor",
            "Nostalgia",
            "Tribalism",
            "FOMO",
            "Convenience",
            "Community",
            "Self-expression",
        ],
        indent=2,
    ),
    "consumer_lenses": json.dumps(
        [
            "Rise of creator trust",
            "Short-form attention",
            "Community-led purchases",
            "Social commerce",
            "Meme consumption",
            "Search behavior changes",
            "AI adoption",
            "Digital fatigue",
            "Authenticity preference",
        ],
        indent=2,
    ),
    "culture_lenses": json.dumps(
        [
            "Meme culture",
            "Creator culture",
            "Parody culture",
            "Nostalgia marketing",
            "Regional pride",
            "IPL culture",
            "Festival culture",
            "Internet humor",
            "Work culture",
            "Startup culture",
        ],
        indent=2,
    ),
    "caption_rules": (
        "Start with a strong statement. Summarize the strategic lesson in 5-7 short lines. "
        "End with: Follow @the_ad_doctor for more marketing breakdowns."
    ),
    "fact_check_rules": (
        "Do not invent data. Every number needs a source. Separate article facts from Ad Doctor opinion. "
        "Never create fake quotes. Do not use unrelated references to force qualification."
    ),
    "designer_rules": (
        "Use one dominant headline, one compact narrative block, one proof visual, source line, and yellow footer cue. "
        "Do not include 'why this slide exists' or internal production notes in the carousel copy."
    ),
}


def get_research_engine_by_key(provider_key: str | None) -> dict[str, str]:
    for engine in RESEARCH_ENGINE_OPTIONS:
        if engine["key"] == provider_key:
            return engine
    return RESEARCH_ENGINE_OPTIONS[0]


def get_research_engine_labels() -> list[str]:
    return [engine["label"] for engine in RESEARCH_ENGINE_OPTIONS]


def get_research_engine_by_label(label: str | None) -> dict[str, str]:
    for engine in RESEARCH_ENGINE_OPTIONS:
        if engine["label"] == label:
            return engine
    return RESEARCH_ENGINE_OPTIONS[0]


def load_research_settings(database_path=DATABASE_PATH) -> dict[str, str]:
    with closing(get_connection(database_path)) as connection:
        row = connection.execute(
            """
            SELECT provider_key, api_key, model, endpoint
            FROM research_provider_settings
            WHERE id = 1
            """
        ).fetchone()
    if not row:
        return dict(DEFAULT_RESEARCH_SETTINGS)
    settings = dict(DEFAULT_RESEARCH_SETTINGS)
    settings.update({key: str(row[key] or "") for key in row.keys()})
    return settings


def save_research_settings(
    *,
    provider_key: str,
    api_key: str,
    model: str,
    endpoint: str,
    database_path=DATABASE_PATH,
) -> dict[str, str]:
    from src.security.resolver import configure_runtime_secret
    engine = get_research_engine_by_key(provider_key)
    if engine.get("env_var"):
        configure_runtime_secret(database_path, engine["env_var"], api_key)
    model = model.strip() or engine["model"]
    endpoint = endpoint.strip() or engine["endpoint"]
    with closing(get_connection(database_path)) as connection:
        connection.execute(
            """
            INSERT INTO research_provider_settings (
                id, provider_key, model, endpoint, updated_at
            )
            VALUES (1, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                provider_key = excluded.provider_key,
                model = excluded.model,
                endpoint = excluded.endpoint,
                updated_at = CURRENT_TIMESTAMP
            """,
            (provider_key, model, endpoint),
        )
        connection.commit()
    return load_research_settings(database_path=database_path)


def load_content_writer_settings(database_path=DATABASE_PATH) -> dict[str, str]:
    with closing(get_connection(database_path)) as connection:
        row = connection.execute(
            """
            SELECT provider_key, enabled, api_key, model, endpoint
            FROM content_writer_settings
            WHERE id = 1
            """
        ).fetchone()
    if not row:
        return dict(DEFAULT_CONTENT_WRITER_SETTINGS)
    settings = dict(DEFAULT_CONTENT_WRITER_SETTINGS)
    settings.update({key: str(row[key] if row[key] is not None else "") for key in row.keys()})
    from src.security.resolver import build_secret_provider
    logical_key = "GEMINI_API_KEY" if settings.get("provider_key") == "gemini" else "OPENAI_API_KEY"
    settings["api_key"] = build_secret_provider(database_path).resolve(logical_key).value or ""
    return settings


def save_content_writer_settings(
    *,
    provider_key: str,
    enabled: bool,
    api_key: str,
    model: str,
    endpoint: str,
    database_path=DATABASE_PATH,
) -> dict[str, str]:
    from src.security.resolver import configure_runtime_secret
    provider_key = provider_key.strip() or "gemini"
    configure_runtime_secret(database_path, "GEMINI_API_KEY" if provider_key == "gemini" else "OPENAI_API_KEY", api_key)
    model = model.strip() or DEFAULT_CONTENT_WRITER_SETTINGS["model"]
    endpoint = endpoint.strip() or DEFAULT_CONTENT_WRITER_SETTINGS["endpoint"]
    with closing(get_connection(database_path)) as connection:
        connection.execute(
            """
            INSERT INTO content_writer_settings (
                id, provider_key, enabled, model, endpoint, updated_at
            )
            VALUES (1, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                provider_key = excluded.provider_key,
                enabled = excluded.enabled,
                model = excluded.model,
                endpoint = excluded.endpoint,
                updated_at = CURRENT_TIMESTAMP
            """,
            (provider_key, 1 if enabled else 0, model, endpoint),
        )
        connection.commit()
    return load_content_writer_settings(database_path=database_path)


def load_content_framework_settings(database_path=DATABASE_PATH) -> dict[str, str]:
    with closing(get_connection(database_path)) as connection:
        row = connection.execute(
            """
            SELECT
                framework_name,
                brand_worldview,
                qualification_rule,
                content_pillars,
                carousel_structure,
                tone_rules,
                psychology_lenses,
                consumer_lenses,
                culture_lenses,
                caption_rules,
                fact_check_rules,
                designer_rules
            FROM content_framework_settings
            WHERE id = 1
            """
        ).fetchone()
    if not row:
        return dict(DEFAULT_CONTENT_FRAMEWORK)
    settings = dict(DEFAULT_CONTENT_FRAMEWORK)
    settings.update({key: str(row[key] or "") for key in row.keys()})
    return settings


def save_content_framework_settings(
    *,
    framework_name: str,
    brand_worldview: str,
    qualification_rule: str,
    content_pillars: str,
    carousel_structure: str,
    tone_rules: str,
    psychology_lenses: str,
    consumer_lenses: str,
    culture_lenses: str,
    caption_rules: str,
    fact_check_rules: str,
    designer_rules: str,
    database_path=DATABASE_PATH,
) -> dict[str, str]:
    with closing(get_connection(database_path)) as connection:
        connection.execute(
            """
            INSERT INTO content_framework_settings (
                id,
                framework_name,
                brand_worldview,
                qualification_rule,
                content_pillars,
                carousel_structure,
                tone_rules,
                psychology_lenses,
                consumer_lenses,
                culture_lenses,
                caption_rules,
                fact_check_rules,
                designer_rules,
                updated_at
            )
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                framework_name = excluded.framework_name,
                brand_worldview = excluded.brand_worldview,
                qualification_rule = excluded.qualification_rule,
                content_pillars = excluded.content_pillars,
                carousel_structure = excluded.carousel_structure,
                tone_rules = excluded.tone_rules,
                psychology_lenses = excluded.psychology_lenses,
                consumer_lenses = excluded.consumer_lenses,
                culture_lenses = excluded.culture_lenses,
                caption_rules = excluded.caption_rules,
                fact_check_rules = excluded.fact_check_rules,
                designer_rules = excluded.designer_rules,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                framework_name.strip() or DEFAULT_CONTENT_FRAMEWORK["framework_name"],
                brand_worldview.strip(),
                qualification_rule.strip(),
                content_pillars.strip(),
                carousel_structure.strip(),
                tone_rules.strip(),
                psychology_lenses.strip(),
                consumer_lenses.strip(),
                culture_lenses.strip(),
                caption_rules.strip(),
                fact_check_rules.strip(),
                designer_rules.strip(),
            ),
        )
        connection.commit()
    return load_content_framework_settings(database_path=database_path)


def reset_content_framework_settings(database_path=DATABASE_PATH) -> dict[str, str]:
    return save_content_framework_settings(
        **DEFAULT_CONTENT_FRAMEWORK,
        database_path=database_path,
    )


def serialize_content_framework_prompt(settings: dict[str, str] | None = None) -> str:
    settings = settings or DEFAULT_CONTENT_FRAMEWORK
    pillars = parse_json_list(settings.get("content_pillars", ""))
    psychology = parse_json_list(settings.get("psychology_lenses", ""))
    consumers = parse_json_list(settings.get("consumer_lenses", ""))
    culture = parse_json_list(settings.get("culture_lenses", ""))
    slides = parse_carousel_structure(settings.get("carousel_structure", ""))

    def bullets(items: list[str]) -> str:
        return "\n".join(f"- {item}" for item in items)

    slide_lines = []
    for slide in slides:
        slide_lines.append(
            f"- Slide {slide.get('slide')}: {slide.get('role')} - {slide.get('purpose')}"
        )

    return "\n\n".join(
        [
            "# " + str(settings.get("framework_name") or DEFAULT_CONTENT_FRAMEWORK["framework_name"]),
            "## Brand Worldview\n" + str(settings.get("brand_worldview") or "").strip(),
            "## Content Pillars\n" + bullets(pillars),
            "## Carousel Structure\n" + "\n".join(slide_lines),
            "## Tone of Voice\n" + str(settings.get("tone_rules") or "").strip(),
            "## Psychology Lenses\n" + bullets(psychology),
            "## Consumer Lenses\n" + bullets(consumers),
            "## Culture Lenses\n" + bullets(culture),
            "## Caption Rules\n" + str(settings.get("caption_rules") or "").strip(),
            "## Fact-check Rules\n" + str(settings.get("fact_check_rules") or "").strip(),
            "## Designer Rules\n" + str(settings.get("designer_rules") or "").strip(),
        ]
    ).strip()


def parse_content_framework_prompt(
    prompt: str,
    fallback: dict[str, str] | None = None,
) -> dict[str, str]:
    fallback = dict(fallback or DEFAULT_CONTENT_FRAMEWORK)
    prompt = str(prompt or "").strip()
    if not prompt:
        return fallback

    name_match = re.search(r"^#\s+(.+)$", prompt, flags=re.MULTILINE)
    section_pattern = re.compile(r"^##\s+(.+?)\s*$", flags=re.MULTILINE)
    matches = list(section_pattern.finditer(prompt))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(prompt)
        sections[match.group(1).strip().lower()] = prompt[start:end].strip()

    def section(name: str, fallback_key: str) -> str:
        return sections.get(name.lower(), "").strip() or fallback[fallback_key]

    def list_json(name: str, fallback_key: str) -> str:
        raw = sections.get(name.lower(), "").strip()
        if not raw:
            return fallback[fallback_key]
        values = [
            line.strip()[2:].strip()
            for line in raw.splitlines()
            if line.strip().startswith("- ") and line.strip()[2:].strip()
        ]
        return json.dumps(values, indent=2) if values else fallback[fallback_key]

    carousel_raw = sections.get("carousel structure", "").strip()
    carousel = []
    for line in carousel_raw.splitlines():
        match = re.match(
            r"^-\s*Slide\s+(\d+)\s*:\s*(.+?)\s+-\s+(.+)$",
            line.strip(),
            flags=re.IGNORECASE,
        )
        if match:
            carousel.append(
                {
                    "slide": int(match.group(1)),
                    "role": match.group(2).strip(),
                    "purpose": match.group(3).strip(),
                }
            )

    parsed = dict(fallback)
    parsed.update(
        {
            "framework_name": name_match.group(1).strip() if name_match else fallback["framework_name"],
            "brand_worldview": section("brand worldview", "brand_worldview"),
            "content_pillars": list_json("content pillars", "content_pillars"),
            "carousel_structure": json.dumps(carousel, indent=2) if carousel else fallback["carousel_structure"],
            "tone_rules": section("tone of voice", "tone_rules"),
            "psychology_lenses": list_json("psychology lenses", "psychology_lenses"),
            "consumer_lenses": list_json("consumer lenses", "consumer_lenses"),
            "culture_lenses": list_json("culture lenses", "culture_lenses"),
            "caption_rules": section("caption rules", "caption_rules"),
            "fact_check_rules": section("fact-check rules", "fact_check_rules"),
            "designer_rules": section("designer rules", "designer_rules"),
        }
    )
    return parsed


def parse_json_list(value: str, fallback: list[str] | None = None) -> list[str]:
    fallback = fallback or []
    value = str(value or "").strip()
    if not value:
        return fallback
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, list):
        clean_items = []
        for item in parsed:
            if isinstance(item, dict):
                text = str(item.get("role") or item.get("title") or item.get("name") or "").strip()
            else:
                text = str(item).strip()
            if text:
                clean_items.append(text)
        return clean_items or fallback
    return [line.strip("- ").strip() for line in value.splitlines() if line.strip()] or fallback


def parse_carousel_structure(value: str) -> list[dict[str, Any]]:
    value = str(value or "").strip()
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, list):
        slides = []
        for index, item in enumerate(parsed, start=1):
            if isinstance(item, dict):
                role = str(item.get("role") or item.get("title") or f"Slide {index}").strip()
                purpose = str(item.get("purpose") or "").strip()
            else:
                role = str(item).strip()
                purpose = ""
            if role:
                slides.append({"slide": index, "role": role, "purpose": purpose})
        return slides
    slides = []
    for index, line in enumerate([line.strip("- ").strip() for line in value.splitlines() if line.strip()], start=1):
        if " - " in line:
            left, right = line.split(" - ", 1)
        elif "—" in line:
            left, right = line.split("—", 1)
        else:
            left, right = line, ""
        if re.match(r"^\s*slide\s+\d+\s*$", left, flags=re.IGNORECASE):
            role, purpose = right, ""
        else:
            role, purpose = left, right
        role = re.sub(r"^\s*slide\s+\d+\s*", "", role, flags=re.IGNORECASE).strip(" :-")
        slides.append({"slide": index, "role": role or f"Slide {index}", "purpose": purpose.strip()})
    return slides
