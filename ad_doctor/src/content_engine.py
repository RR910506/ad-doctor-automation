from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import re

from src.config import DATABASE_PATH, TEMPLATE_DIR
from src.db import (
    get_connection,
    get_latest_locked_run_id,
    insert_content_output,
    list_content_outputs,
    list_dissection_outputs,
    list_research_dossiers,
    log_run_event,
)
from src.google_workspace_store import (
    is_google_draft_store_enabled,
    sync_content_outputs_to_google_workspace,
)
from src.openai_content_writer import write_final_draft_with_chatgpt
from src.settings_engine import (
    load_content_framework_settings,
    parse_carousel_structure,
    parse_json_list,
)
from src.topic_matcher import is_same_news_event, topic_similarity
from src.case_files import get_case_lifecycle_service
from src.domain.workflow import WorkflowStatus


@dataclass(frozen=True)
class ContentGenerationSummary:
    run_id: str
    blog_drafts_created: int
    social_drafts_created: int


AD_DOCTOR_PILLARS = (
    "Campaign Surgery",
    "Agency Diagnosis",
    "Platform Pulse",
    "Consumer Pulse",
    "Culture Scan",
    "Brand Checkup",
    "Doctor's Verdict",
    "Marketing Prescription",
)


PSYCHOLOGY_SIGNALS = {
    "Identity": ("identity", "self-expression", "personal brand", "generation", "youth"),
    "Belonging": ("community", "fans", "tribe", "creator", "social", "shared"),
    "Status": ("premium", "luxury", "award", "cannes", "ranking", "most-awarded"),
    "Validation": ("review", "award", "recognition", "rank", "approved"),
    "Aspiration": ("athlete", "olympic", "global", "premium", "future"),
    "Trust": ("trust", "authentic", "creator", "review", "transparency"),
    "Fear": ("risk", "privacy", "uncertain", "decline", "challenge"),
    "Curiosity": ("ai", "new", "launch", "experiment", "innovation"),
    "Humor": ("meme", "parody", "humor", "funny", "internet"),
    "Nostalgia": ("nostalgia", "retro", "classic", "heritage"),
    "Tribalism": ("fans", "sports", "ipl", "football", "community"),
    "FOMO": ("limited", "trend", "viral", "deadline", "launch"),
    "Convenience": ("simplify", "automation", "ai", "agent", "easy"),
    "Community": ("community", "creator", "fans", "social"),
    "Self-expression": ("self-expression", "creator", "personal", "identity"),
}


CONSUMER_BEHAVIOR_SIGNALS = {
    "Rise of creator trust": ("creator", "influencer", "ugc"),
    "Short-form attention": ("short-form", "reels", "tiktok", "shorts"),
    "Community-led purchases": ("community", "fans", "social commerce"),
    "Social commerce": ("commerce", "shopping", "retail", "social commerce"),
    "Meme consumption": ("meme", "parody", "humor", "internet culture"),
    "Search behavior changes": ("search", "google", "discovery"),
    "AI adoption": ("ai", "agent", "automation", "generative"),
    "Digital fatigue": ("fatigue", "overload", "attention"),
    "Authenticity preference": ("authentic", "trust", "transparent", "creator"),
}


CULTURE_SIGNALS = {
    "Meme culture": ("meme", "parody", "internet humor"),
    "Creator culture": ("creator", "influencer", "ugc"),
    "Nostalgia marketing": ("nostalgia", "retro", "classic", "heritage"),
    "Regional pride": ("regional", "local", "india", "language"),
    "IPL culture": ("ipl", "cricket"),
    "Festival culture": ("festival", "diwali", "christmas", "eid"),
    "Internet humor": ("humor", "parody", "meme"),
    "Work culture": ("work", "office", "career", "employee"),
    "Startup culture": ("startup", "founder", "funding"),
    "Awards culture": ("cannes", "award", "ranking", "creative rankings"),
    "AI culture": ("ai", "agent", "generative", "automation"),
}


def generate_content_outputs(
    database_path=DATABASE_PATH,
    run_id: str | None = None,
) -> ContentGenerationSummary:
    requested_run_id = run_id
    run_id = requested_run_id or datetime.now(UTC).strftime("run_%Y%m%d_%H%M%S")
    blog_template_path = TEMPLATE_DIR / "blog_template.md"
    social_template_path = TEMPLATE_DIR / "social_post_template.md"
    blog_template = read_template(blog_template_path)
    social_template = read_template(social_template_path)
    framework_settings = load_content_framework_settings(database_path=database_path)
    blog_count = 0
    social_count = 0
    lifecycle = get_case_lifecycle_service(database_path)
    completed_shadow: list[tuple[int, int]] = []
    if lifecycle:
        with closing(get_connection(database_path)) as shadow_connection:
            shadow_locked_run_id = requested_run_id or get_latest_locked_run_id(shadow_connection)
            shadow_topic_rows = shadow_connection.execute(
                "SELECT DISTINCT lt.topic_id FROM locked_topics lt JOIN dissection_outputs d ON d.locked_topic_id=lt.id WHERE lt.run_id=?",
                (shadow_locked_run_id,),
            ).fetchall() if shadow_locked_run_id else []
        for shadow_row in shadow_topic_rows:
            topic_id = int(shadow_row["topic_id"])
            try:
                lifecycle.transition_topic(topic_id, WorkflowStatus.CONTENT_PENDING, run_id=run_id, stage="content", reason="Content generation started.")
            except Exception as exc:
                with closing(get_connection(database_path)) as error_connection:
                    log_run_event(error_connection, run_id, "case_shadow_content", "error", f"Content-start shadow transition failed for topic {topic_id}: {exc}", json.dumps({"run_id":run_id,"topic_id":topic_id,"stage":"content_start","error_type":type(exc).__name__}))
                    error_connection.commit()

    with closing(get_connection(database_path)) as connection:
        locked_run_id = requested_run_id or get_latest_locked_run_id(connection)
        dissections = (
            list_dissection_outputs(connection, run_id=locked_run_id)
            if locked_run_id
            else []
        )
        dossiers = (
            list_research_dossiers(connection, run_id=locked_run_id)
            if locked_run_id
            else []
        )
        dossiers_by_locked_topic_id = {
            int(dossier["locked_topic_id"]): json.loads(dossier["dossier_json"])
            for dossier in dossiers
        }
        for dissection in dissections:
            locked_topic_id = int(dissection["locked_topic_id"])
            topic_row = connection.execute("SELECT topic_id FROM locked_topics WHERE id = ?", (locked_topic_id,)).fetchone()
            topic_id = int(topic_row["topic_id"]) if topic_row else 0
            dossier = dossiers_by_locked_topic_id.get(locked_topic_id, {})
            social_draft = build_social_draft(
                dossier,
                dissection["output_markdown"],
                social_template,
                framework_settings=framework_settings,
            )
            if is_rejected_social_draft(social_draft):
                insert_content_output(
                    connection,
                    locked_topic_id=locked_topic_id,
                    content_type="social",
                    template_version=social_template_path.name,
                    draft_markdown=social_draft,
                    approval_status="rejected",
                )
                continue

            blog_draft = build_blog_draft(dossier, dissection["output_markdown"], blog_template)
            blog_writer = write_final_draft_with_chatgpt(
                content_type="blog",
                local_draft=blog_draft,
                dossier=dossier,
                dissection_markdown=str(dissection["output_markdown"] or ""),
                framework_settings=framework_settings,
                database_path=database_path,
            )
            social_writer = write_final_draft_with_chatgpt(
                content_type="social",
                local_draft=social_draft,
                dossier=dossier,
                dissection_markdown=str(dissection["output_markdown"] or ""),
                framework_settings=framework_settings,
                database_path=database_path,
            )
            blog_draft = blog_writer.draft_markdown
            social_draft = social_writer.draft_markdown
            blog_id = insert_content_output(
                connection,
                locked_topic_id=locked_topic_id,
                content_type="blog",
                template_version=blog_template_path.name,
                draft_markdown=blog_draft,
            )
            social_id = insert_content_output(
                connection,
                locked_topic_id=locked_topic_id,
                content_type="social",
                template_version=social_template_path.name,
                draft_markdown=social_draft,
            )
            blog_count += 1
            social_count += 1
            if topic_id:
                completed_shadow.append((topic_id, int(social_id or blog_id)))

        summary = ContentGenerationSummary(
            run_id=run_id,
            blog_drafts_created=blog_count,
            social_drafts_created=social_count,
        )
        google_synced = 0
        if is_google_draft_store_enabled():
            google_synced = sync_content_outputs_to_google_workspace(
                list_content_outputs(connection, run_id=run_id),
                database_path=database_path,
            )
        log_run_event(
            connection,
            run_id,
                "phase_9_content",
                "complete",
                (
                    f"Generated {blog_count} blog draft(s) and {social_count} social draft(s). "
                    f"Synced {google_synced} draft(s) to Google Workspace."
                ),
            json.dumps(
                {
                    **summary.__dict__,
                    "google_synced": google_synced,
                    "content_writer": "openai_chatgpt_when_configured",
                }
            ),
        )
        connection.commit()
    if lifecycle:
        for topic_id, draft_id in completed_shadow:
            try:
                lifecycle.link_draft(topic_id, draft_id, run_id=run_id)
                from src.services.legacy_content_quality_shadow import LegacyContentQualityShadowService
                LegacyContentQualityShadowService(database_path).mirror(topic_id,draft_id,run_id)
            except Exception as exc:
                with closing(get_connection(database_path)) as connection:
                    log_run_event(connection, run_id, "case_shadow_content", "error", f"Draft-link shadow transition failed for topic {topic_id}: {exc}", json.dumps({"run_id":run_id,"topic_id":topic_id,"stage":"content_complete","draft_id":draft_id,"error_type":type(exc).__name__}))
                    connection.commit()
    return summary


def is_rejected_social_draft(draft_markdown: str) -> bool:
    return (
        "**Status:** Rejected" in draft_markdown
        or "Qualification Status: Not Qualified" in draft_markdown
        or "Qualification Status:\nNot Qualified" in draft_markdown
    )


def read_template(template_path) -> str:
    return template_path.read_text(encoding="utf-8") if template_path.exists() else ""


def build_blog_draft(dossier: dict[str, object], dissection_markdown: str, template_text: str) -> str:
    topic = str(dossier.get("topic") or "Untitled campaign topic")
    reference_articles = select_topic_reference_articles(dossier)
    source_names = [str(article.get("source_name")) for article in reference_articles if article.get("source_name")]
    references = format_reference_articles(reference_articles)
    if references == "No topic-specific source URLs available.":
        references = "\n".join(f"- {url}" for url in dossier.get("source_urls", [])) or "- No source URLs available."
    evidence_text = build_evidence_text(dossier, dissection_markdown, reference_articles)
    pillar = infer_content_pillar(topic, dossier, evidence_text)
    core_insight = build_core_insight(dossier, evidence_text, pillar)
    consumer_analysis = build_signal_analysis(
        evidence_text,
        CONSUMER_BEHAVIOR_SIGNALS,
        "consumer behavior",
        "The consumer behavior shift needs stronger evidence before making a definitive claim.",
    )
    cultural_analysis = build_signal_analysis(
        evidence_text,
        CULTURE_SIGNALS,
        "culture",
        "The cultural force is not directly named in stored coverage, so treat this as a hypothesis.",
    )
    marketing_analysis = build_marketing_analysis(pillar, dossier)
    doctor_verdict = build_doctor_verdict(pillar, dossier, core_insight)
    prescription = build_marketing_prescription(pillar, dossier, consumer_analysis)
    template_note = build_template_note(template_text, "blog")
    return f"""# {topic}

{template_note}

## Content Engine
Approved-source Ad Doctor framework generator. Perplexity is not required for this draft.

## Qualification
- Status: {"Qualified" if count_unique_sources(reference_articles) >= 3 else "Needs review"}
- Sources count: {count_unique_sources(reference_articles)}
- Sources used: {format_source_names(source_names)}
- Research mode: {get_research_mode_label(dossier)}

## Approved-Source Intelligence Summary
{build_content_research_summary(dossier, reference_articles)}

## Why This Matters
{build_why_topic_matters(pillar, dossier, core_insight)}

## Topic Snapshot
- Brand: {dossier.get("brand_details", "To be confirmed")}
- Campaign: {dossier.get("campaign_name", "To be confirmed")}
- Platforms: {", ".join(dossier.get("media_platform_usage", [])) or "To be confirmed"}
- Content pillar: {pillar}

## Diagnostic Layers
### Business
{summarize_claim(clean_context_or_default(dossier, "market_context", "The business context is not strongly confirmed in stored coverage yet."), 320)}

### Marketing
{paragraph_from_analysis(marketing_analysis, limit=420)}

### Consumer
{consumer_analysis}

### Culture
{cultural_analysis}

## Doctor's Verdict
{doctor_verdict}

## Marketing Prescription
{prescription}

## Fact Discipline
Article facts come from approved-source coverage. Ad Doctor diagnosis is interpretation. If data is unavailable, the draft must say data unavailable instead of inventing numbers.

## Source References
{references}

---

## Campaign Dissection Notes
{dissection_markdown}
"""


def build_social_draft(
    dossier: dict[str, object],
    dissection_markdown: str,
    template_text: str,
    framework_settings: dict[str, str] | None = None,
) -> str:
    _ = template_text
    topic = str(dossier.get("topic") or "Untitled campaign topic")
    reference_articles = select_topic_reference_articles(dossier)
    source_names = [str(article.get("source_name")) for article in reference_articles if article.get("source_name")]
    coverage_count = count_unique_sources(reference_articles)
    references = format_reference_articles(reference_articles)
    if coverage_count < 3:
        return build_rejected_social_draft(
            topic,
            coverage_count,
            references,
            reason=(
                "The stored topic had fewer than 3 topic-specific references after removing irrelevant links. "
                "Do not use unrelated articles to force qualification."
            ),
        )

    evidence_text = build_evidence_text(dossier, dissection_markdown, reference_articles)
    pillar = enforce_framework_pillar(
        infer_content_pillar(str(topic), dossier, evidence_text),
        framework_settings,
    )
    core_insight = build_core_insight(dossier, evidence_text, pillar)
    strategic_insight = build_core_strategic_insight(dossier, core_insight)
    psychology_analysis = build_signal_analysis(
        evidence_text,
        PSYCHOLOGY_SIGNALS,
        "human psychology",
        "The strongest psychology layer is not explicitly confirmed in stored coverage.",
    )
    consumer_analysis = build_signal_analysis(
        evidence_text,
        CONSUMER_BEHAVIOR_SIGNALS,
        "consumer behavior",
        "The consumer behavior shift needs stronger evidence before making a definitive claim.",
    )
    cultural_analysis = build_signal_analysis(
        evidence_text,
        CULTURE_SIGNALS,
        "culture",
        "The cultural force is not directly named in stored coverage, so treat this as a hypothesis.",
    )
    marketing_analysis = build_marketing_analysis(pillar, dossier)
    doctor_verdict = build_doctor_verdict(pillar, dossier, core_insight)
    prescription = build_marketing_prescription(pillar, dossier, consumer_analysis)
    carousel = build_carousel(
        pillar=pillar,
        topic=str(topic),
        dossier=dossier,
        reference_articles=reference_articles,
        core_insight=core_insight,
        psychology_analysis=psychology_analysis,
        consumer_analysis=consumer_analysis,
        cultural_analysis=cultural_analysis,
        marketing_analysis=marketing_analysis,
        doctor_verdict=doctor_verdict,
        prescription=prescription,
        framework_settings=framework_settings,
    )
    caption = build_caption(
        topic=str(topic),
        core_insight=strategic_insight,
        prescription=prescription,
        framework_settings=framework_settings,
    )

    return "\n\n".join(
        [
            "Topic Name: " + topic,
            "Qualification Status: Qualified",
            f"Sources Count: {coverage_count}",
            "Sources Used: " + format_source_names(source_names),
            "Research Summary:\n" + build_content_research_summary(dossier, reference_articles),
            "Core Strategic Insight:\n" + strategic_insight,
            "Selected Content Pillar: " + pillar,
            "Framework Used: " + get_framework_name(framework_settings),
            "Carousel Draft:",
            carousel,
            "Caption:\n" + caption,
            "Hashtags:\n" + build_hashtags(pillar, evidence_text),
            "Designer Notes:\n" + build_designer_notes(pillar, topic, reference_articles, framework_settings),
            "Fact-check Notes:\n" + build_fact_check_notes(reference_articles, dossier, framework_settings),
            "Risk Notes:\n" + build_risk_notes(dossier),
            "Source References:\n\n" + references,
        ]
    )


def build_template_note(template_text: str, template_type: str) -> str:
    if template_text and "Placeholder" not in template_text:
        return f"Configured {template_type} template applied."
    return f"Placeholder {template_type} template applied. Replace the template file with the final format when ready."


def build_rejected_social_draft(
    topic: object,
    coverage_count: int,
    references: str,
    reason: str | None = None,
) -> str:
    reason = reason or (
        "The Ad Doctor does not create publishable social content for topics with fewer than "
        "3 unique approved source websites."
    )
    return "\n\n".join(
        [
            "Topic Name: " + str(topic),
            "Qualification Status: Not Qualified",
            f"Sources Count: {coverage_count}",
            "Sources Used: Not enough exact-topic approved sources.",
            "Research Summary: Not requested because this topic failed qualification.",
            "Core Strategic Insight: Do not create content for topics with fewer than 3 exact-topic approved sources.",
            "Decision: Do not publish. Wait until more credible approved-source coverage is available.",
            "Fact-check Notes:\n" + reason,
            "Risk Notes:\nCreating a carousel here would force a story from weak evidence.",
            "Source References:\n\n" + references,
        ]
    )


def get_perplexity_research(dossier: dict[str, object]) -> dict[str, object]:
    research = dossier.get("perplexity_research")
    return research if isinstance(research, dict) else {}


def build_perplexity_research_summary(dossier: dict[str, object]) -> str:
    return build_content_research_summary(dossier, select_topic_reference_articles(dossier))


def build_content_research_summary(
    dossier: dict[str, object],
    reference_articles: list[dict[str, object]] | None = None,
) -> str:
    reference_articles = reference_articles or select_topic_reference_articles(dossier)
    local_summary = build_approved_source_intelligence_summary(dossier, reference_articles)
    research = get_perplexity_research(dossier)
    summary = clean_research_value(research.get("summary"))
    status = str(research.get("status") or "not_available")
    if status == "completed" and summary:
        return f"{local_summary}\n\nAI research add-on: {summary}\n\nResearch status: completed."
    return (
        f"{local_summary}\n\n"
        "AI research add-on: unavailable. The content engine is using exact-topic approved-source "
        f"coverage only.\n\nResearch status: {status}."
    )


def build_approved_source_intelligence_summary(
    dossier: dict[str, object],
    reference_articles: list[dict[str, object]],
) -> str:
    topic = str(dossier.get("topic") or "Untitled topic")
    source_names = [str(article.get("source_name") or article.get("article_url") or "") for article in reference_articles]
    headlines = top_reference_headlines(reference_articles, limit=4)
    event_copy = build_verified_event_copy(topic, reference_articles)
    data_points = format_verified_data_points(get_verified_data_points(dossier))
    return "\n".join(
        [
            f"Research mode: {get_research_mode_label(dossier)}.",
            build_source_evidence_summary(reference_articles),
            f"Confirmed event: {event_copy}",
            f"Source signal: {'; '.join(headlines) if headlines else 'Headline detail unavailable.'}",
            f"Sources used: {format_source_names(source_names)}.",
            f"Verified data: {data_points}.",
            "Boundary: no external web research was used unless AI research status says completed.",
        ]
    )


def get_research_mode_label(dossier: dict[str, object]) -> str:
    research = get_perplexity_research(dossier)
    status = str(research.get("status") or "").lower()
    if status == "completed":
        return "Perplexity-augmented approved-source synthesis"
    return "Approved-source local synthesis"


def build_core_strategic_insight(dossier: dict[str, object], local_core_insight: str) -> str:
    research = get_perplexity_research(dossier)
    marketing_lesson = clean_research_value(research.get("marketing_lesson"))
    brand_strategy = clean_research_value(research.get("brand_strategy"))
    if marketing_lesson and marketing_lesson.lower() != "data unavailable":
        return marketing_lesson
    if brand_strategy and brand_strategy.lower() != "data unavailable":
        return brand_strategy
    return local_core_insight


def clean_research_value(value: object, limit: int = 900) -> str:
    text = normalize_whitespace(str(value or ""))
    if not text or text.lower() in {"none", "null", "data unavailable"}:
        return ""
    return truncate_text(text, limit)


def research_or_local(
    dossier: dict[str, object],
    research_key: str,
    local_key: str,
    fallback: str,
    *,
    limit: int = 260,
) -> str:
    research = get_perplexity_research(dossier)
    research_value = clean_research_value(research.get(research_key), limit=limit)
    if research_value:
        return research_value
    local_value = clean_context_or_default(dossier, local_key, fallback)
    return truncate_text(local_value or fallback, limit)


def format_slide_block(index: int, slide: dict[str, str]) -> str:
    return "\n".join(
        [
            f"Slide {index}:",
            "Headline: " + slide["headline"],
            "Body:\n" + slide["body"],
            "Visual Suggestion:\n" + slide["visual_suggestion"],
            "Bold Words: " + slide["bold_words"],
            "Source: " + slide["source"],
        ]
    )


def build_hook_headline(topic: str, dossier: dict[str, object], pillar: str) -> str:
    subject = infer_carousel_subject(topic, dossier)
    if pillar == "Platform Pulse":
        return f"Why {subject} Is Bigger Than A Platform Update"
    if pillar == "Agency Diagnosis":
        return f"Why {subject} Is Bigger Than An Agency Headline"
    if pillar == "Culture Scan":
        return f"Why {subject} Became A Culture Signal"
    if pillar == "Consumer Pulse":
        return f"Why {subject} Is Bigger Than A Consumer Trend"
    if pillar == "Brand Checkup":
        return f"Why {subject} Is Bigger Than A Brand Move"
    return f"How {subject} Became A Marketing Signal"


def infer_carousel_subject(topic: str, dossier: dict[str, object]) -> str:
    brand = clean_field(dossier.get("brand_details"), limit=70)
    if brand and not brand.lower().startswith("to be confirmed"):
        return brand
    topic = normalize_whitespace(topic)
    topic = re.sub(r"\s+(launches|appoints|wins|crowns|sets|expands|introduces|unveils)\b.*$", "", topic, flags=re.IGNORECASE)
    return truncate_text(topic or "This Brand Move", 70)


def build_short_body(*sentences: str, limit: int = 360) -> str:
    clean_sentences = [normalize_whitespace(sentence) for sentence in sentences if normalize_whitespace(sentence)]
    return truncate_text(" ".join(clean_sentences), limit)


def build_visual_suggestion(
    *,
    topic: str,
    slide_role: str,
    source_note: str,
    data_used: bool = False,
) -> str:
    image_type = "Approved publication screenshot or campaign/brand visual evidence"
    prompt = f"{topic} {slide_role} advertising marketing news campaign visual"
    layout = "Large headline at top, short body under it, one proof image below, source note under image."
    data_note = (
        "If using the number on this slide, place the source URL or publication name beside the data."
        if data_used
        else "Use exact-topic visual evidence only; avoid unrelated brand or award images."
    )
    return (
        f"Image type: {image_type}. "
        f"Image search prompt: {prompt}. "
        f"Layout idea: {layout} "
        f"Source note: {source_note}. {data_note}"
    )


def build_bold_words(headline: str, body: str, subject: str) -> str:
    candidates = [subject]
    candidates.extend(re.findall(r"\b\d+(?:\.\d+)?\s?(?:%|million|billion|mn|bn)\b", body, flags=re.IGNORECASE))
    for phrase in (
        "marketing signal",
        "consumer shift",
        "business outcome",
        "strategic move",
        "Ad Doctor's view",
        "data unavailable",
    ):
        if phrase.lower() in f"{headline} {body}".lower():
            candidates.append(phrase)
    return ", ".join(dict.fromkeys(candidate for candidate in candidates if candidate)) or "Key strategic phrase"


def build_slide_source(reference_articles: list[dict[str, object]], note: str = "") -> str:
    source_cue = build_source_cue(reference_articles)
    return f"{source_cue}. {note}".strip()


def get_verified_data_points(dossier: dict[str, object]) -> list[dict[str, str]]:
    research = get_perplexity_research(dossier)
    raw_points = research.get("verified_data_points")
    points: list[dict[str, str]] = []
    if isinstance(raw_points, list):
        for point in raw_points:
            if isinstance(point, dict):
                claim = normalize_whitespace(str(point.get("claim") or point.get("data") or point.get("point") or ""))
                source = normalize_whitespace(str(point.get("source") or point.get("url") or ""))
            else:
                claim = normalize_whitespace(str(point or ""))
                source = ""
            if claim and claim.lower() != "data unavailable":
                points.append({"claim": claim, "source": source or "source unavailable"})
    performance = clean_field(dossier.get("performance_data"), limit=260)
    if performance:
        points.append({"claim": performance, "source": "Approved source coverage attached to this topic."})
    return points


def format_verified_data_points(points: list[dict[str, str]]) -> str:
    if not points:
        return "data unavailable"
    return "; ".join(f"{point['claim']} (Source: {point['source']})" for point in points[:3])


def build_fact_check_notes(
    reference_articles: list[dict[str, object]],
    dossier: dict[str, object],
    framework_settings: dict[str, str] | None = None,
) -> str:
    data_points = get_verified_data_points(dossier)
    lines = [
        f"Qualification uses only exact-topic references from {format_source_names([str(article.get('source_name') or '') for article in reference_articles])}.",
        "Every source in Sources Used must cover the same topic, not merely the same brand.",
        f"Numbers/data used: {format_verified_data_points(data_points)}.",
        "Article facts are sourced; Ad Doctor diagnosis is interpretation based on those facts.",
        "No fake quotes are allowed.",
    ]
    framework_rules = get_framework_text(framework_settings, "fact_check_rules", "")
    if framework_rules:
        lines.append(f"Framework rule: {framework_rules}")
    return "\n".join(lines)


def build_risk_notes(dossier: dict[str, object]) -> str:
    research = get_perplexity_research(dossier)
    status = str(research.get("status") or "not_available")
    risks = [
        f"AI research status: {status}.",
        "If status is unavailable or error, the draft is based only on approved-source local synthesis.",
        "Do not use unrelated source links to strengthen the story.",
        "If a claim sounds like brand intent, keep it framed as Ad Doctor interpretation unless source coverage confirms it.",
    ]
    return "\n".join(risks)


def format_social_section(section_number: int, title: str, body: str) -> str:
    return f"## SECTION {section_number}: {title}\n\n{body.strip()}"


def build_at_a_glance_table(
    *,
    pillar: str,
    coverage_count: int,
    source_names: list[object],
) -> str:
    return "\n".join(
        [
            "## At A Glance",
            "",
            "| Field | Detail |",
            "|---|---|",
            f"| Selected pillar | {pillar} |",
            f"| Qualification | Passed 3-source rule |",
            f"| Coverage | {coverage_count} unique approved source(s) |",
            f"| Evidence base | {format_source_names(source_names)} |",
        ]
    )


def format_source_names(source_names: list[object]) -> str:
    clean_names = [str(name).strip() for name in source_names if str(name).strip()]
    unique_names = list(dict.fromkeys(clean_names))
    if not unique_names:
        return "Approved sources attached in references."
    if len(unique_names) <= 6:
        return ", ".join(unique_names)
    return f"{', '.join(unique_names[:6])}, and {len(unique_names) - 6} more"


def get_framework_name(framework_settings: dict[str, str] | None) -> str:
    if not framework_settings:
        return "The Ad Doctor Diagnostic Carousel"
    return str(framework_settings.get("framework_name") or "The Ad Doctor Diagnostic Carousel")


def get_framework_pillars(framework_settings: dict[str, str] | None) -> list[str]:
    if not framework_settings:
        return list(AD_DOCTOR_PILLARS)
    return parse_json_list(
        str(framework_settings.get("content_pillars") or ""),
        fallback=list(AD_DOCTOR_PILLARS),
    )


def enforce_framework_pillar(pillar: str, framework_settings: dict[str, str] | None) -> str:
    allowed_pillars = get_framework_pillars(framework_settings)
    if pillar in allowed_pillars:
        return pillar
    if "Campaign Surgery" in allowed_pillars:
        return "Campaign Surgery"
    return allowed_pillars[0] if allowed_pillars else "Campaign Surgery"


def get_framework_slide_roles(framework_settings: dict[str, str] | None) -> list[dict[str, object]]:
    if not framework_settings:
        return []
    return parse_carousel_structure(str(framework_settings.get("carousel_structure") or ""))


def get_framework_text(
    framework_settings: dict[str, str] | None,
    key: str,
    fallback: str,
) -> str:
    if not framework_settings:
        return fallback
    text = normalize_whitespace(str(framework_settings.get(key) or ""))
    return text or fallback


def extract_framework_cta(framework_settings: dict[str, str] | None) -> str:
    default_cta = "Follow @the_ad_doctor for more marketing breakdowns."
    caption_rules = get_framework_text(framework_settings, "caption_rules", "")
    if not caption_rules:
        return default_cta
    match = re.search(r"End with:\s*[\"']?([^\"'\n]+)", caption_rules, flags=re.IGNORECASE)
    if match:
        return normalize_whitespace(match.group(1)).rstrip(".") + "."
    if "follow @the_ad_doctor" in caption_rules.lower():
        return default_cta
    return default_cta


def select_topic_reference_articles(dossier: dict[str, object]) -> list[dict[str, object]]:
    topic = str(dossier.get("topic") or "")
    raw_articles = dossier.get("source_articles") or []
    articles = [
        article
        for article in raw_articles
        if isinstance(article, dict)
        and article.get("article_url")
        and is_topic_specific_reference(topic, article)
    ]
    unique_articles = dedupe_reference_articles(articles)
    if unique_articles:
        return unique_articles
    fallback_articles = [
        {"source_name": infer_source_name_from_url(str(url)), "headline": "", "article_url": str(url)}
        for url in dossier.get("source_urls", [])
        if str(url).strip() and is_topic_specific_url(topic, str(url))
    ]
    return dedupe_reference_articles(fallback_articles)


def is_topic_specific_reference(topic: str, article: dict[str, object]) -> bool:
    headline = str(article.get("headline") or "")
    meta_description = str(article.get("meta_description") or "")
    article_url = str(article.get("article_url") or "")
    candidate_text = " ".join(part for part in [headline, meta_description, url_slug_text(article_url)] if part)
    if not topic or not candidate_text:
        return False
    if is_same_news_event(topic, headline or candidate_text):
        return True
    if headline and topic_similarity(topic, headline) >= 0.62:
        return True
    if topic_similarity(topic, candidate_text) >= 0.68:
        return True
    return False


def is_topic_specific_url(topic: str, article_url: str) -> bool:
    slug_text = url_slug_text(article_url)
    return bool(slug_text and topic_similarity(topic, slug_text) >= 0.68)


def dedupe_reference_articles(articles: list[dict[str, object]]) -> list[dict[str, object]]:
    deduped = []
    seen_urls = set()
    for article in articles:
        url = normalize_url_for_reference(str(article.get("article_url") or ""))
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        deduped.append(article)
    return deduped


def count_unique_sources(articles: list[dict[str, object]]) -> int:
    source_keys = {
        normalize_source_key(str(article.get("source_name") or article.get("article_url") or ""))
        for article in articles
    }
    return len({key for key in source_keys if key})


def format_reference_articles(articles: list[dict[str, object]]) -> str:
    if not articles:
        return "No topic-specific source URLs available."
    rows = []
    for article in articles:
        source_name = str(article.get("source_name") or infer_source_name_from_url(str(article.get("article_url") or "")))
        headline = str(article.get("headline") or "Source article").strip()
        article_url = str(article.get("article_url") or "").strip()
        rows.append(f"- **{source_name}:** [{headline}]({article_url})")
    return "\n".join(rows)


def normalize_source_key(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"^https?://(www\.)?", "", value)
    value = value.split("/")[0]
    return value


def normalize_url_for_reference(value: str) -> str:
    return value.strip().lower().rstrip("/")


def infer_source_name_from_url(article_url: str) -> str:
    domain = normalize_source_key(article_url)
    return domain or "Approved source"


def url_slug_text(article_url: str) -> str:
    cleaned = re.sub(r"^https?://", "", article_url.lower())
    cleaned = re.sub(r"[?#].*$", "", cleaned)
    parts = cleaned.split("/")
    slug = " ".join(parts[1:]) if len(parts) > 1 else cleaned
    slug = re.sub(r"\.[a-z0-9]+$", "", slug)
    slug = re.sub(r"[-_+/]+", " ", slug)
    slug = re.sub(r"\b[a-z0-9]{12,}\b", " ", slug)
    return normalize_whitespace(slug)


def build_evidence_text(
    dossier: dict[str, object],
    dissection_markdown: str,
    reference_articles: list[dict[str, object]] | None = None,
) -> str:
    reference_articles = reference_articles or []
    parts = [
        str(dossier.get("topic", "")),
        " ".join(str(article.get("headline") or "") for article in reference_articles),
        " ".join(str(article.get("meta_description") or "") for article in reference_articles),
        clean_field(dossier.get("market_context")),
        clean_field(dossier.get("strategic_insight")),
        clean_field(dossier.get("creative_idea")),
        clean_field(dossier.get("execution")),
        clean_field(dossier.get("campaign_objective")),
        clean_field(dossier.get("audience")),
        ", ".join(str(platform) for platform in dossier.get("media_platform_usage", [])),
        dissection_markdown,
    ]
    return normalize_whitespace(" ".join(parts))


def infer_content_pillar(topic: str, dossier: dict[str, object], evidence_text: str) -> str:
    topic_text = topic.lower()
    text = f"{topic} {evidence_text}".lower()
    agency_terms = (
        "agency review",
        "agency of record",
        "aor",
        "appoints",
        "hires",
        "acquires",
        "merger",
        "publicis",
        "wpp",
        "dentsu",
        "mccann",
        "fcb",
    )
    platform_terms = ("instagram", "linkedin", "youtube", "tiktok", "snapchat", "meta", "google", "x ", "platform")
    consumer_terms = ("consumer", "shopping", "behavior", "trust", "creator", "community")
    culture_terms = ("culture", "meme", "parody", "ipl", "festival", "cannes", "nostalgia")
    brand_terms = ("rebrand", "reposition", "brand", "review", "launches global media review")
    if any(term in topic_text for term in platform_terms):
        return "Platform Pulse"
    if any(term in topic_text for term in culture_terms):
        return "Culture Scan"
    if any(term in topic_text for term in agency_terms):
        return "Agency Diagnosis"
    if any(term in topic_text for term in consumer_terms):
        return "Consumer Pulse"
    if any(term in topic_text for term in brand_terms):
        return "Brand Checkup"
    if any(term in text for term in platform_terms):
        return "Platform Pulse"
    if any(term in text for term in culture_terms):
        return "Culture Scan"
    if any(term in text for term in agency_terms):
        return "Agency Diagnosis"
    if any(term in text for term in consumer_terms):
        return "Consumer Pulse"
    if any(term in text for term in brand_terms):
        return "Brand Checkup"
    return "Campaign Surgery"


def build_core_insight(dossier: dict[str, object], evidence_text: str, pillar: str) -> str:
    brand = clean_field(dossier.get("brand_details"), limit=80) or "the brand"
    topic = clean_field(dossier.get("topic"), limit=160)
    if pillar == "Agency Diagnosis":
        return (
            f"This is an ecosystem signal: {brand} is not only making a campaign or award move, "
            "it is revealing how brands use agencies, media partners, and specialist capabilities to solve growth, commerce, and relevance pressure."
        )
    if pillar == "Platform Pulse":
        return (
            "This is a platform-pressure signal. When platforms change, brands and agencies must rethink how attention is earned, distributed, measured, and trusted."
        )
    if pillar == "Consumer Pulse":
        return "This topic points to a consumer behavior shift. The useful question is not what happened, but what it reveals about how people now trust, search, buy, or participate."
    if pillar == "Culture Scan":
        return "This topic matters because culture is shaping the marketing condition. The brand move is a response to cultural codes, community behavior, or shared attention."
    if pillar == "Brand Checkup":
        return f"This is a brand-strategy symptom. {brand} appears to be responding to pressure around relevance, growth, trust, positioning, or consumer attention."
    if topic:
        return f"This campaign should be read as a strategic response, not a standalone announcement: {topic}."
    return "The deeper marketing signal needs more source evidence before a strong diagnosis can be made."


def build_signal_analysis(
    evidence_text: str,
    signal_map: dict[str, tuple[str, ...]],
    layer_name: str,
    fallback: str,
) -> str:
    matched = []
    lowered = evidence_text.lower()
    for signal, markers in signal_map.items():
        if any(marker in lowered for marker in markers):
            matched.append(signal)
    if not matched:
        return fallback
    signal_list = ", ".join(matched[:4])
    return (
        f"Primary signal(s): {signal_list}.\n\n"
        f"**Diagnosis:** The {layer_name} layer should explain the human reason behind the marketing move, "
        "not merely name the trend."
    )


def build_marketing_analysis(pillar: str, dossier: dict[str, object]) -> str:
    brand = clean_field(dossier.get("brand_details"), limit=80) or "the brand"
    platforms = [str(platform) for platform in dossier.get("media_platform_usage", []) if str(platform).strip()]
    platform_text = ", ".join(platforms) if platforms else "the confirmed media/platform mix"
    if pillar == "Agency Diagnosis":
        return (
            f"- **Business lens:** {brand} is being evaluated through the ecosystem around it: agencies, partners, commerce, data, and media capability.\n"
            "- **Marketing lens:** The important question is what capability gap or growth pressure this move is solving.\n"
            "- **Industry lens:** For agencies, this is a reminder that relevance is built through problem-solving, not visibility alone."
        )
    if pillar == "Platform Pulse":
        return (
            "- **Business lens:** Platform changes alter how brands reach people and how agencies plan work.\n"
            "- **Marketing lens:** The feature or update matters only if it changes behavior, measurement, targeting, or creative distribution.\n"
            "- **Industry lens:** Agencies need to translate platform mechanics into strategic implications."
        )
    if pillar == "Brand Checkup":
        return (
            f"- **Business lens:** {brand} appears to be managing a brand or growth pressure.\n"
            "- **Marketing lens:** The move should be judged by whether it improves relevance, trust, clarity, or conversion.\n"
            "- **Industry lens:** Brand activity is increasingly judged through ecosystem performance, not just communication."
        )
    return (
        f"- **Business lens:** The topic reflects a market pressure that needs a strategic response.\n"
        f"- **Marketing lens:** The execution must connect the idea with consumer behavior and {platform_text}.\n"
        "- **Industry lens:** Marketers should study the underlying condition before copying the visible tactic."
    )


def build_why_topic_matters(pillar: str, dossier: dict[str, object], core_insight: str) -> str:
    brand = clean_field(dossier.get("brand_details")) or "the brand"
    if pillar == "Platform Pulse":
        return f"It shows how platform change can reshape brand behavior, agency planning, and consumer attention.\n\n**Ad Doctor read:** {core_insight}"
    if pillar == "Agency Diagnosis":
        return f"It explains why an agency or partner move matters beyond a profile: what market gap it responds to and what marketers can learn.\n\n**Ad Doctor read:** {core_insight}"
    if pillar == "Consumer Pulse":
        return f"It reveals how people may be changing the way they trust, search, consume, or participate in marketing.\n\n**Ad Doctor read:** {core_insight}"
    if pillar == "Culture Scan":
        return f"It shows how culture becomes a force that brands and agencies must interpret before they communicate.\n\n**Ad Doctor read:** {core_insight}"
    if pillar == "Brand Checkup":
        return f"It shows what {brand} may be trying to solve in the market and what that reveals about brand strategy.\n\n**Ad Doctor read:** {summarize_claim(core_insight)}"
    return f"It gives marketers evidence to understand the strategy beneath a campaign or brand move.\n\n**Ad Doctor read:** {summarize_claim(core_insight)}"


def build_doctor_verdict(pillar: str, dossier: dict[str, object], core_insight: str) -> str:
    brand = clean_field(dossier.get("brand_details")) or "the brand"
    if pillar == "Platform Pulse":
        return "This is not just a feature update. It is a pressure shift in how brands earn attention, distribute content, and measure relevance."
    if pillar == "Agency Diagnosis":
        return "The agency matters if its work solves a real market problem, not merely because it has visible clients or awards."
    if pillar == "Consumer Pulse":
        return "The real signal is not the campaign output. It is the behavior change underneath it."
    if pillar == "Culture Scan":
        return "Culture is not a backdrop here. It is the operating system that gives the marketing move meaning."
    if pillar == "Brand Checkup":
        return f"{brand} is showing a strategic symptom. The important question is whether the move solves a consumer or market tension."
    return f"{brand} should be judged by the strength of the insight, not only the visibility of the execution. {summarize_claim(core_insight)}"


def build_marketing_prescription(pillar: str, dossier: dict[str, object], consumer_analysis: str) -> str:
    if pillar == "Platform Pulse":
        return "Marketers should translate platform changes into behavior implications before making media or creative decisions."
    if pillar == "Agency Diagnosis":
        return "Agencies should define the market problem they are built to solve, then prove that philosophy through work."
    if pillar == "Consumer Pulse":
        return "Start with the behavior change, then build the creative, channel, and measurement system around it."
    if pillar == "Culture Scan":
        return "Brands should participate in culture only when they understand the codes, tensions, and community expectations behind it."
    if pillar == "Brand Checkup":
        return "Diagnose the business pressure first, then decide whether the brand move is strategic medicine or surface treatment."
    return f"Use the consumer layer as the operating brief: {consumer_analysis}"


def build_slide_copy(lead: str, points: list[str]) -> str:
    clean_lead = truncate_text(normalize_whitespace(lead), 140)
    clean_points = []
    for point in points:
        for split_point in split_slide_points(point):
            split_point = normalize_slide_point(split_point)
            if split_point and split_point not in clean_points:
                clean_points.append(split_point)
    if not clean_points:
        clean_points = ["More source evidence is needed before making a stronger diagnosis."]
    bullet_lines = [f"- {point}" for point in clean_points[:3]]
    return "\n".join([f"**{clean_lead}**", "", *bullet_lines])


def split_slide_points(point: str) -> list[str]:
    text = str(point or "").strip()
    if not text:
        return []
    fragments = [fragment.strip() for fragment in re.split(r"\n+", text) if fragment.strip()]
    return fragments or [text]


def normalize_slide_point(point: str) -> str:
    point = normalize_whitespace(str(point or ""))
    point = re.sub(r"^\s*[-*]\s+", "", point)
    point = re.sub(r"\*\*(Diagnosis|Business lens|Marketing lens|Industry lens):\*\*", r"\1:", point)
    point = point.replace("..", ".")
    return truncate_text(point, 230)


def build_source_evidence_summary(reference_articles: list[dict[str, object]]) -> str:
    coverage_count = count_unique_sources(reference_articles)
    source_names = [str(article.get("source_name") or article.get("article_url") or "") for article in reference_articles]
    return (
        f"Evidence base: {coverage_count} approved source(s) covering the same topic: "
        f"{format_source_names(source_names)}."
    )


def build_source_cue(reference_articles: list[dict[str, object]]) -> str:
    source_names = [str(article.get("source_name") or article.get("article_url") or "") for article in reference_articles]
    return f"Approved coverage from {format_source_names(source_names)}"


def build_verified_event_copy(topic: str, reference_articles: list[dict[str, object]]) -> str:
    headline = next(
        (
            str(article.get("headline")).strip()
            for article in reference_articles
            if str(article.get("headline") or "").strip()
        ),
        "",
    )
    verified_event = headline if headline and is_same_news_event(topic, headline) else topic
    return f"Approved-source coverage confirms the same event: {truncate_text(verified_event, 190)}."


def build_hook_title(topic: str) -> str:
    _ = topic
    return "Why This Became A Marketing Signal"


def build_intro_narrative(topic: str, pillar: str, reference_articles: list[dict[str, object]]) -> str:
    coverage_count = count_unique_sources(reference_articles)
    return (
        f"When **{truncate_text(topic, 150)}** appears across **{coverage_count} approved sources**, "
        "it becomes more than a headline. The Ad Doctor question is not *what happened*. "
        f"It is *why this became a {pillar.lower()} signal* and what it reveals about brands, "
        "platforms, culture, agencies, and audiences."
    )


def build_problem_narrative(pillar: str, market_context: str, core_insight: str) -> str:
    return (
        f"The visible event is only the surface. Underneath it is a pressure point: **{summarize_claim(market_context, 180)}** "
        f"Ad Doctor reads this through **{pillar}** because {lowercase_first(summarize_claim(core_insight, 230))}"
    )


def build_research_evidence_narrative(
    reference_articles: list[dict[str, object]],
    event_copy: str,
    source_summary: str,
) -> str:
    headlines = top_reference_headlines(reference_articles, limit=3)
    if headlines:
        research_line = "; ".join(headlines)
    else:
        research_line = event_copy
    return (
        f"The research base is narrow by design: only links that match the same topic are allowed. "
        f"{source_summary} The strongest confirmed signals are **{truncate_text(research_line, 260)}**. "
        "Anything outside this topic is excluded from the draft, even if it mentions the same brand."
    )


def build_business_diagnosis_narrative(pillar: str, market_context: str, execution_context: str) -> str:
    if pillar == "Platform Pulse":
        pressure = "a distribution and attention problem"
    elif pillar == "Agency Diagnosis":
        pressure = "a capability and partner-ecosystem problem"
    elif pillar == "Culture Scan":
        pressure = "a cultural relevance problem"
    elif pillar == "Consumer Pulse":
        pressure = "a behavior-change problem"
    elif pillar == "Brand Checkup":
        pressure = "a brand relevance and trust problem"
    else:
        pressure = "a campaign effectiveness problem"
    return (
        f"The business diagnosis is **{pressure}**. {summarize_claim(market_context, 220)} "
        f"The confirmed execution layer is: {lowercase_first(summarize_claim(execution_context, 220))}"
    )


def build_signal_narrative(layer_name: str, analysis: str) -> str:
    signals = extract_primary_signals(analysis)
    diagnosis = extract_diagnosis_sentence(analysis)
    if signals:
        return (
            f"The {layer_name} layer points to **{signals}**. {diagnosis} "
            "This is the part marketers should study before copying the visible tactic."
        )
    return (
        f"The {layer_name} layer is not fully quantified in stored coverage yet. "
        f"Ad Doctor should treat it carefully: {summarize_claim(analysis, 260)}"
    )


def build_culture_platform_narrative(cultural_analysis: str, dossier: dict[str, object]) -> str:
    platforms = [str(platform) for platform in dossier.get("media_platform_usage", []) if str(platform).strip()]
    platform_line = (
        f"The platform context includes **{', '.join(platforms)}**. "
        if platforms
        else "The platform context is not fully confirmed in stored coverage. "
    )
    return (
        f"{build_signal_narrative('culture', cultural_analysis)} {platform_line}"
        "The lesson is that culture and distribution decide whether a marketing move travels beyond industry news."
    )


def build_marketing_lesson_narrative(
    marketing_analysis: str,
    creative_context: str,
    performance_context: str,
) -> str:
    analysis_sentence = paragraph_from_analysis(marketing_analysis, limit=260)
    return (
        f"The marketing lesson is that **strategy has to explain the result before content celebrates it**. "
        f"{analysis_sentence} Creative proof: {lowercase_first(summarize_claim(creative_context, 180))} "
        f"Impact discipline: {lowercase_first(summarize_claim(performance_context, 160))}"
    )


def build_ad_doctor_stand_narrative(doctor_verdict: str, prescription: str) -> str:
    return (
        f"**Ad Doctor's stand:** {summarize_claim(doctor_verdict, 260)} "
        f"The prescription is simple: **{lowercase_first(summarize_claim(prescription, 220))}** "
        "The goal is not to report the event. The goal is to diagnose what marketers should learn from it."
    )


def top_reference_headlines(reference_articles: list[dict[str, object]], limit: int = 3) -> list[str]:
    headlines = []
    for article in reference_articles:
        headline = normalize_whitespace(str(article.get("headline") or ""))
        if headline and headline not in headlines:
            headlines.append(truncate_text(headline, 90))
        if len(headlines) >= limit:
            break
    return headlines


def extract_primary_signals(analysis: str) -> str:
    match = re.search(r"Primary signal\(s\):\s*(.+?)(?:\.|$)", analysis)
    return match.group(1).strip() if match else ""


def extract_diagnosis_sentence(analysis: str) -> str:
    match = re.search(r"\*\*Diagnosis:\*\*\s*(.+)$", analysis, flags=re.DOTALL)
    if match:
        return summarize_claim(match.group(1), 240)
    return summarize_claim(analysis, 240)


def paragraph_from_analysis(analysis: str, limit: int = 320) -> str:
    fragments = [clean_analysis_fragment(fragment) for fragment in split_slide_points(analysis)]
    clean_fragments = [fragment for fragment in fragments if fragment]
    return truncate_text(" ".join(clean_fragments[:2]), limit)


def clean_analysis_fragment(fragment: str) -> str:
    fragment = normalize_whitespace(str(fragment or ""))
    fragment = re.sub(r"^\s*[-*]\s+", "", fragment)
    fragment = re.sub(r"\*\*(Diagnosis|Business lens|Marketing lens|Industry lens):\*\*", r"\1:", fragment)
    return fragment


def lowercase_first(text: str) -> str:
    text = text.strip()
    if not text:
        return text
    if re.match(r"[A-Z]{2}(?:\b|-)", text):
        return text
    return text[0].lower() + text[1:]


def clean_context_or_default(dossier: dict[str, object], key: str, fallback: str) -> str:
    value = clean_field(dossier.get(key), limit=220)
    if not value or looks_like_scraped_headline_dump(value):
        return fallback
    return value


def looks_like_scraped_headline_dump(text: str) -> bool:
    words = re.findall(r"[A-Za-z0-9]+", text)
    if len(words) < 26:
        return False
    unique_ratio = len({word.lower() for word in words}) / max(1, len(words))
    repeated_brand = any(text.lower().count(term) >= 3 for term in ("coca-cola", "google", "wpp", "publicis"))
    has_sentence_shape = bool(re.search(r"[.!?]\s+[A-Z]", text))
    return unique_ratio < 0.68 or repeated_brand or not has_sentence_shape


def build_carousel(
    *,
    pillar: str,
    topic: str,
    dossier: dict[str, object],
    reference_articles: list[dict[str, object]],
    core_insight: str,
    psychology_analysis: str,
    consumer_analysis: str,
    cultural_analysis: str,
    marketing_analysis: str,
    doctor_verdict: str,
    prescription: str,
    framework_settings: dict[str, str] | None = None,
) -> str:
    source_cue = build_source_cue(reference_articles)
    event_copy = build_verified_event_copy(topic, reference_articles)
    subject = infer_carousel_subject(topic, dossier)
    market_context = clean_context_or_default(
        dossier,
        "market_context",
        "The market pressure is attention fragmentation: brands need moves that create relevance, trust, and measurable growth.",
    )
    problem_context = research_or_local(
        dossier,
        "market_context",
        "market_context",
        market_context,
    )
    strategic_move = research_or_local(
        dossier,
        "brand_strategy",
        "strategic_insight",
        "The strategic move needs to be read as a response to market pressure, not as a standalone announcement.",
    )
    consumer_shift = clean_research_value(get_perplexity_research(dossier).get("consumer_shift"), limit=260)
    if not consumer_shift:
        consumer_shift = summarize_claim(consumer_analysis, 260) or "The consumer shift is not fully quantified in available coverage yet."
    impact_context = research_or_local(
        dossier,
        "business_impact",
        "performance_data",
        "data unavailable",
        limit=260,
    )
    cultural_context = clean_research_value(get_perplexity_research(dossier).get("cultural_relevance"), limit=220)
    marketing_lesson = clean_research_value(get_perplexity_research(dossier).get("marketing_lesson"), limit=280)
    if not marketing_lesson:
        marketing_lesson = summarize_claim(core_insight, 280)
    verified_data_points = get_verified_data_points(dossier)
    verified_data_text = format_verified_data_points(verified_data_points)
    data_used = bool(verified_data_points)
    slides = [
        {
            "headline": build_hook_headline(topic, dossier, pillar),
            "body": build_short_body(
                f"{event_copy}",
                f"The Ad Doctor read: this is a {pillar.lower()} story because the useful question is why marketers should care.",
                "The visible event is the symptom. The strategy underneath is the story.",
            ),
            "source": build_slide_source(reference_articles, "Qualification and event facts."),
            "role": "hook",
            "data_used": False,
        },
        {
            "headline": "The Problem Was Not The Headline",
            "body": build_short_body(
                f"Before this move, the pressure point was: {problem_context}",
                "That is why Ad Doctor treats it as a market diagnosis, not a news recap.",
            ),
            "source": build_slide_source(reference_articles, "Context from approved coverage and local framework synthesis."),
            "role": "problem context",
            "data_used": False,
        },
        {
            "headline": "The Strategic Move Was The Real Story",
            "body": build_short_body(
                f"{subject} changed the marketing equation by addressing this strategic layer: {strategic_move}",
                "The move matters only if it changes positioning, packaging, distribution, behavior, or cultural relevance.",
            ),
            "source": build_slide_source(reference_articles, "Strategy is interpreted from exact-topic coverage."),
            "role": "strategic move",
            "data_used": False,
        },
        {
            "headline": "The Audience Signal Changed",
            "body": build_short_body(
                f"Consumer shift: {consumer_shift}",
                "The marketer's job is to understand the behavior underneath the attention.",
            ),
            "source": build_slide_source(reference_articles, "Consumer layer is research synthesis unless a source states it directly."),
            "role": "consumer shift",
            "data_used": False,
        },
        {
            "headline": "The Impact Was Bigger Than Visibility",
            "body": build_short_body(
                f"Brand or market impact: {impact_context}",
                "If the impact is not yet quantified, the draft should stay disciplined and avoid invented growth claims.",
            ),
            "source": build_slide_source(reference_articles, "Impact claims require source support."),
            "role": "brand market impact",
            "data_used": data_used,
        },
        {
            "headline": "Why It Worked",
            "body": build_short_body(
                f"The strategic reason: {marketing_lesson}",
                f"Culture/platform layer: {cultural_context or summarize_claim(cultural_analysis, 180) or 'not strongly quantified yet.'}",
            ),
            "source": build_slide_source(reference_articles, "Ad Doctor interpretation based on approved coverage."),
            "role": "why it worked",
            "data_used": False,
        },
        {
            "headline": "The Business Outcome",
            "body": build_short_body(
                f"Verified data: {verified_data_text}.",
                "If data is unavailable, do not replace it with guesswork. Keep the lesson strategic.",
            ),
            "source": (
                format_verified_data_points(verified_data_points)
                if verified_data_points
                else "data unavailable"
            ),
            "role": "business outcome data",
            "data_used": data_used,
        },
        {
            "headline": "What We Think",
            "body": build_short_body(
                f"Ad Doctor's view: {doctor_verdict}",
                f"Prescription: {prescription}",
                limit=390,
            ),
            "source": "Ad Doctor opinion based on the approved-source evidence base.",
            "role": "ad doctor verdict",
            "data_used": False,
        },
    ]
    framework_roles = get_framework_slide_roles(framework_settings)
    for index, slide in enumerate(slides):
        if index < len(framework_roles):
            role = str(framework_roles[index].get("role") or "").strip()
            purpose = str(framework_roles[index].get("purpose") or "").strip()
            if role:
                slide["role"] = role
            if purpose:
                slide["framework_purpose"] = purpose
        slide["visual_suggestion"] = build_visual_suggestion(
            topic=topic,
            slide_role=str(slide["role"]),
            source_note=str(slide["source"]),
            data_used=bool(slide["data_used"]),
        )
        slide["bold_words"] = build_bold_words(slide["headline"], slide["body"], subject)
    return "\n\n".join(format_slide_block(index, slide) for index, slide in enumerate(slides, start=1))


def build_narrative_slide_markdown(
    index: int,
    total_slides: int,
    title: str,
    narrative: str,
    source: str,
) -> str:
    lines = [
        f"### Slide {index}/{total_slides}: {title}",
        "",
        f"**{title}**",
        "",
        narrative,
    ]
    if source:
        lines.extend(["", f"**Source:** {source}"])
    return "\n".join(lines)


def build_slide_markdown(
    index: int,
    title: str,
    on_slide: str,
    purpose: str,
    visual: str,
) -> str:
    _ = (purpose, visual)
    return "\n".join(
        [
            f"### Slide {index}: {title}",
            "",
            on_slide,
        ]
    )


def build_media_diagnosis(dossier: dict[str, object]) -> str:
    platforms = [str(platform) for platform in dossier.get("media_platform_usage", []) if str(platform).strip()]
    if not platforms:
        return "Media and platform usage is not confirmed in stored coverage."
    return (
        f"The media signal sits across {', '.join(platforms)}. "
        "The question is whether the channel choice supports the consumer behavior behind the idea."
    )


def build_caption(
    topic: str,
    core_insight: str,
    prescription: str,
    framework_settings: dict[str, str] | None = None,
) -> str:
    cta = extract_framework_cta(framework_settings)
    return (
        f"{topic} is not just another industry update.\n\n"
        f"{summarize_claim(core_insight, 240)}\n"
        "The important question is not only what happened.\n"
        "It is what this reveals about brands, people, platforms, culture, and market pressure.\n"
        f"{summarize_claim(prescription, 220)}\n\n"
        f"{cta}"
    )


def build_hashtags(pillar: str, evidence_text: str) -> str:
    tags = ["#TheAdDoctor", "#MarketingIntelligence", "#BrandStrategy", "#Advertising"]
    if pillar == "Agency Diagnosis":
        tags.extend(["#AgencyLife", "#CreativeStrategy"])
    elif pillar == "Platform Pulse":
        tags.extend(["#PlatformStrategy", "#DigitalMarketing"])
    elif pillar == "Consumer Pulse":
        tags.extend(["#ConsumerBehavior", "#MarketingPsychology"])
    elif pillar == "Culture Scan":
        tags.extend(["#CultureMarketing", "#CulturalStrategy"])
    elif pillar == "Brand Checkup":
        tags.extend(["#BrandBuilding", "#MarketingStrategy"])
    else:
        tags.extend(["#CampaignStrategy", "#CreativeEffectiveness"])
    if "ai" in evidence_text.lower():
        tags.append("#AIMarketing")
    return " ".join(dict.fromkeys(tags))


def build_research_notes(reference_articles: list[dict[str, object]], dossier: dict[str, object]) -> str:
    headlines = top_reference_headlines(reference_articles, limit=5)
    source_names = [str(article.get("source_name") or article.get("article_url") or "") for article in reference_articles]
    research_points = [
        f"Only exact-topic references are used: {format_source_names(source_names)}.",
        f"Confirmed reference headlines: {'; '.join(headlines) if headlines else 'No headline text available.'}",
        f"Stored market context: {clean_context_or_default(dossier, 'market_context', 'Not strongly confirmed in stored coverage yet.')}",
        f"Stored execution context: {clean_context_or_default(dossier, 'execution', 'Not strongly confirmed in stored coverage yet.')}",
    ]
    return "\n".join(f"- {point}" for point in research_points)


def build_editorial_checks(doctor_verdict: str, prescription: str) -> str:
    checks = [
        "Does the draft explain why the topic matters, not just what happened?",
        "Does every carousel slide make one clear diagnostic point?",
        f"Ad Doctor stand: {summarize_claim(doctor_verdict, 240)}",
        f"Marketing prescription: {summarize_claim(prescription, 240)}",
    ]
    return "\n".join(f"- {check}" for check in checks)


def build_designer_notes(
    pillar: str,
    topic: str | None = None,
    reference_articles: list[dict[str, object]] | None = None,
    framework_settings: dict[str, str] | None = None,
) -> str:
    topic_text = f" for {topic}" if topic else ""
    source_note = ""
    if reference_articles is not None:
        source_names = [str(article.get("source_name") or "") for article in reference_articles]
        source_note = f" Use exact-topic source visuals from {format_source_names(source_names)}."
    designer_rule = get_framework_text(
        framework_settings,
        "designer_rules",
        (
            "Each slide needs one dominant headline, one short body, one proof visual, and one source cue. "
            "Follow the Ad Doctor reference style: bold headline, compact narrative, image proof, source line, yellow footer cue."
        ),
    )
    return (
        f"Design this as a {pillar} diagnosis{topic_text}, not as a news carousel. "
        f"{designer_rule} "
        "Prioritize the slide hierarchy over decoration."
        + source_note
    )


def build_visual_references(pillar: str, dossier: dict[str, object]) -> str:
    platforms = ", ".join(str(platform) for platform in dossier.get("media_platform_usage", [])) or "source screenshots"
    return (
        "- Approved publication headline screenshots\n"
        "- Campaign, brand, agency, or platform visual evidence where available\n"
        f"- Platform context: {platforms}\n"
        "- Use visual metaphors from diagnosis: signal map, pressure point, prescription card"
    )


def build_fun_fact(dossier: dict[str, object]) -> str:
    performance = clean_field(dossier.get("performance_data"))
    platforms = [str(platform) for platform in dossier.get("media_platform_usage", []) if str(platform).strip()]
    if performance:
        return summarize_claim(performance)
    if platforms:
        return f"Platform clue: source coverage mentions {', '.join(platforms)}."
    source_names = [str(source) for source in dossier.get("source_names", []) if str(source).strip()]
    if source_names:
        return f"Coverage clue: this topic appeared across {len(set(source_names))} approved source(s)."
    return "No fun fact is confirmed in stored source coverage yet."


def clean_field(value: object, limit: int = 320) -> str:
    text = normalize_whitespace(str(value or ""))
    unavailable_markers = (
        "not available",
        "not confirmed",
        "to be confirmed",
        "no performance data found",
    )
    if not text or any(marker in text.lower() for marker in unavailable_markers):
        return ""
    return truncate_text(text, limit)


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def truncate_text(text: str, limit: int) -> str:
    text = normalize_whitespace(text)
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def summarize_claim(text: str, limit: int = 220) -> str:
    text = normalize_whitespace(text)
    if not text:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    first_sentence = sentences[0] if sentences else text
    if len(first_sentence) < 60 and len(sentences) > 1:
        first_sentence = f"{first_sentence} {sentences[1]}"
    return truncate_text(first_sentence, limit)


def generate_content() -> None:
    raise NotImplementedError("Use generate_content_outputs.")
