from __future__ import annotations

from contextlib import closing
import json
import os
import re

import streamlit as st

import src.perplexity_research as perplexity_research
from src.approval_engine import set_content_approval
from src.automation_engine import (
    get_automation_settings,
    install_or_update_daily_task,
    parse_schedule_times,
    save_automation_settings,
)
from src.app_scheduler import (
    get_app_scheduler_status,
    list_automation_run_history,
    start_app_scheduler,
)
from src.config import APP_NAME, DATA_DIR
from src.article_fetcher import fetch_articles
from src.content_engine import generate_content_outputs
from src.coverage_backfill import expand_topic_coverage_from_stored_articles
from src.demo_runner import run_demo_pipeline
from src.dissection_engine import run_campaign_dissections
from src.db import (
    count_articles,
    count_topics,
    get_connection,
    get_latest_locked_run_id,
    list_active_sources,
    initialize_database,
    list_article_source_counts,
    list_approval_queue,
    list_articles_by_source,
    list_content_outputs,
    list_dissection_outputs,
    list_locked_topics,
    list_research_dossiers,
    list_run_logs,
    list_sources,
    list_topic_scores,
    list_topics,
)
from src.export_engine import export_approved_outputs
from src.notification_engine import (
    list_app_users,
    list_notification_events,
    load_smtp_settings,
    save_smtp_settings,
    send_test_notification_email,
    upsert_app_user,
)
from src.pipeline_runner import run_real_pipeline
from src.prioritization_engine import prioritize_topics
from src.qualification_engine import qualify_topics
from src.research_engine import build_research_dossiers
from src.source_manager import (
    SOURCE_COLUMNS,
    google_sheet_url_to_csv_url,
    import_sources_from_file,
    import_sources_from_google_sheet,
)
from src.topic_extractor import extract_topics
from src.topic_lock_engine import lock_top_topics


DASHBOARD_SECTIONS = [
    ("Source Input", "Import approved sources from XLS/XLSX/CSV files or Google Sheet CSV exports."),
    ("Automation & Alerts", "Daily schedule and notification settings."),
    ("Article Fetch Status", "Phase 3 will show RSS, crawl, and manual URL fetch results."),
    ("Extracted Articles", "Phase 3 will display stored article metadata and text availability."),
    ("Extracted Topics", "Phase 4 will show exact news-event topics extracted from articles."),
    ("Qualified Topics", "Phase 5 will apply the 3 unique-source coverage gate."),
    ("Topic Score Breakdown", "Phase 5 will explain coverage, credibility, freshness, richness, and references."),
    ("Locked Topics", "Phase 6 will lock selected topics for each run."),
    ("Research Dossier", "Phase 7 will collect cited research for locked topics."),
    ("Campaign Dissection", "Phase 8 will use the configurable dissection roadmap."),
    ("Blog Draft", "Phase 9 will generate blog drafts from the configured template."),
    ("Social Media Draft", "Phase 9 will generate social post drafts from the configured template."),
    ("Approval Queue", "Phase 10 will require human approval before final output."),
    ("Export Options", "Phase 10 will export approved outputs."),
]

ADVANCED_PIPELINE_SECTIONS = [
    "Article Fetch Status",
    "Extracted Articles",
    "Extracted Topics",
    "Qualified Topics",
    "Topic Score Breakdown",
    "Locked Topics",
    "Research Dossier",
    "Campaign Dissection",
]

MAIN_PAGES = [
    "Content Studio",
    "Sources",
    "Schedule & Keys",
    "Review & Export",
    "Advanced Pipeline",
]


def render_dashboard_shell() -> None:
    st.set_page_config(page_title=APP_NAME, layout="wide")
    initialize_database()
    apply_runtime_provider_settings()
    start_app_scheduler()
    inject_app_styles()

    with st.sidebar:
        st.header(APP_NAME)
        st.caption("Marketing intelligence workspace")
        page = st.radio("Workspace", MAIN_PAGES, label_visibility="collapsed")
        render_user_session_controls()
        st.divider()
        render_provider_status()
        if st.button("Run content automation", type="primary", use_container_width=True):
            run_full_automation_from_ui()

    st.title(APP_NAME)
    st.caption("Diagnose marketing stories. Generate blog and social drafts.")

    if page == "Content Studio":
        render_content_studio()
    elif page == "Sources":
        render_sources_page()
    elif page == "Schedule & Keys":
        render_schedule_and_keys()
    elif page == "Review & Export":
        render_review_export_page()
    elif page == "Advanced Pipeline":
        render_advanced_pipeline()


def inject_app_styles() -> None:
    st.markdown(
        """
        <style>
        [data-testid="stSidebar"] .stButton button { width: 100%; }
        .ad-muted { color: rgba(250,250,250,.68); font-size: .95rem; }
        .ad-section-note {
            border: 1px solid rgba(250,250,250,.12);
            border-radius: 8px;
            padding: .85rem 1rem;
            background: rgba(255,255,255,.03);
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_content_studio() -> None:
    st.subheader("Content Studio")
    st.write("Review the latest Ad Doctor drafts without digging through the pipeline.")
    render_content_snapshot()
    render_credit_usage_summary()

    action_columns = st.columns([1, 1, 2])
    with action_columns[0]:
        if st.button("Generate drafts only", type="primary"):
            try:
                apply_runtime_provider_settings()
                summary = generate_content_outputs()
                st.success(
                    f"Generated {summary.blog_drafts_created} blog draft(s) and "
                    f"{summary.social_drafts_created} social draft(s)."
                )
            except Exception as exc:
                st.error(str(exc))
    with action_columns[1]:
        if st.button("Run full scan now"):
            run_full_automation_from_ui()

    social_tab, blog_tab = st.tabs(["Social Media Drafts", "Blog Drafts"])
    with social_tab:
        render_content_drafts("social", show_generate_button=False)
    with blog_tab:
        render_content_drafts("blog", show_generate_button=False)


def render_sources_page() -> None:
    st.subheader("Sources")
    st.write("Add the approved websites or Google Sheet that Ad Doctor is allowed to scan.")
    render_source_input()


def render_schedule_and_keys() -> None:
    st.subheader("Schedule & Keys")
    schedule_tab, provider_tab, email_tab = st.tabs(["Run Times", "API Keys & Models", "Email Alerts"])
    with schedule_tab:
        render_automation_alerts(show_recipients=False, show_smtp_status=False)
    with provider_tab:
        render_provider_settings()
    with email_tab:
        render_email_alert_settings()


def render_review_export_page() -> None:
    st.subheader("Review & Export")
    review_tab, export_tab = st.tabs(["Approve Drafts", "Export"])
    with review_tab:
        render_approval_queue()
    with export_tab:
        render_export_options()


def render_advanced_pipeline() -> None:
    st.subheader("Advanced Pipeline")
    st.write("Use this area only when you need to inspect scraping, qualification, or research details.")
    tabs = st.tabs(ADVANCED_PIPELINE_SECTIONS)
    renderers = {
        "Article Fetch Status": render_article_fetch_status,
        "Extracted Articles": render_extracted_articles,
        "Extracted Topics": render_extracted_topics,
        "Qualified Topics": render_qualified_topics,
        "Topic Score Breakdown": render_topic_score_breakdown,
        "Locked Topics": render_locked_topics,
        "Research Dossier": render_research_dossier,
        "Campaign Dissection": render_campaign_dissection,
    }
    for tab, section_name in zip(tabs, ADVANCED_PIPELINE_SECTIONS):
        with tab:
            renderers[section_name]()


def run_full_automation_from_ui() -> None:
    with st.spinner("Scanning approved sources, qualifying topics, and preparing drafts..."):
        try:
            apply_runtime_provider_settings()
            settings = get_automation_settings()
            summary = run_real_pipeline(
                fetch_source_time_budget_seconds=settings.fetch_source_time_budget_seconds,
            )
            st.session_state["last_pipeline_summary"] = summary.__dict__
            st.success(
                f"Done: {summary.qualified_topics} qualified topic(s), "
                f"{summary.blog_drafts} blog draft(s), {summary.social_drafts} social draft(s)."
            )
        except Exception as exc:
            st.error(str(exc))


def render_content_snapshot() -> None:
    with closing(get_connection()) as connection:
        article_total = count_articles(connection)
        qualified_total = count_topics(connection, status="qualified")
        outputs = list_content_outputs(connection)
        approval_items = list_approval_queue(connection)
    social_ready = sum(
        1
        for output in outputs
        if output["content_type"] == "social" and output["approval_status"] != "rejected"
    )
    blog_ready = sum(
        1
        for output in outputs
        if output["content_type"] == "blog" and output["approval_status"] != "rejected"
    )
    pending_review = sum(1 for item in approval_items if item["approval_status"] == "pending")
    metrics = st.columns(4)
    metrics[0].metric("Social drafts", social_ready)
    metrics[1].metric("Blog drafts", blog_ready)
    metrics[2].metric("Pending review", pending_review)
    metrics[3].metric("Qualified topics", qualified_total)
    st.caption(f"{article_total} approved-source articles are stored for analysis.")


def render_credit_usage_summary() -> None:
    usage = estimate_credit_usage()
    metrics = st.columns(4)
    metrics[0].metric("Estimated API credits", usage["estimated_api_credits"])
    metrics[1].metric("Research calls", usage["research_calls"])
    metrics[2].metric("Tracked tokens", usage["total_tokens"])
    metrics[3].metric("Local draft credits", 0)
    st.caption(
        "Credits are tracked from provider responses when available. "
        "Local scraping and template drafting do not consume AI credits."
    )


def estimate_credit_usage() -> dict[str, int]:
    with closing(get_connection()) as connection:
        latest_locked_run_id = get_latest_locked_run_id(connection)
        dossiers = (
            list_research_dossiers(connection, run_id=latest_locked_run_id)
            if latest_locked_run_id
            else []
        )
    research_calls = 0
    total_tokens = 0
    for dossier in dossiers:
        try:
            payload = json.loads(dossier.get("dossier_json") or "{}")
        except (TypeError, ValueError):
            payload = {}
        if payload.get("status") == "completed":
            research_calls += 1
        usage = payload.get("usage") if isinstance(payload, dict) else None
        if isinstance(usage, dict):
            total_tokens += int(
                usage.get("total_tokens")
                or usage.get("total")
                or usage.get("tokens")
                or 0
            )
    return {
        "estimated_api_credits": research_calls,
        "research_calls": research_calls,
        "total_tokens": total_tokens,
    }


def render_user_session_controls() -> None:
    st.subheader("Your Access")
    with st.form("user_session_form"):
        email = st.text_input("Email", value=st.session_state.get("ad_doctor_user_email", ""))
        display_name = st.text_input(
            "Display name",
            value=st.session_state.get("ad_doctor_user_name", ""),
        )
        notifications_enabled = st.checkbox(
            "Email me when content is ready",
            value=st.session_state.get("ad_doctor_notifications_enabled", True),
        )
        submitted = st.form_submit_button("Save")
    if not submitted:
        return
    try:
        upsert_app_user(
            email,
            display_name=display_name or None,
            notifications_enabled=notifications_enabled,
            mark_logged_in=True,
        )
        st.session_state["ad_doctor_user_email"] = email.strip().lower()
        st.session_state["ad_doctor_user_name"] = display_name
        st.session_state["ad_doctor_notifications_enabled"] = notifications_enabled
        st.success("Session saved.")
    except Exception as exc:
        st.error(str(exc))


def render_provider_status() -> None:
    research_model = st.session_state.get("ad_doctor_research_model", "sonar-pro")
    has_perplexity_key = bool(st.session_state.get("ad_doctor_perplexity_api_key"))
    has_openrouter_key = bool(st.session_state.get("ad_doctor_openrouter_api_key"))
    st.caption(f"Research model: {research_model}")
    if has_perplexity_key or has_openrouter_key:
        st.success("Session API key ready.")
    else:
        st.info("Using environment keys or local fallback.")


def render_provider_settings() -> None:
    st.write("Add API keys and choose models for this browser session. Keys are not written to code.")
    research_provider = st.selectbox(
        "Research engine",
        ["Perplexity"],
        index=0,
        help="Perplexity is currently wired into the research step.",
    )
    perplexity_key = st.text_input(
        "Perplexity API key",
        value=st.session_state.get("ad_doctor_perplexity_api_key", ""),
        type="password",
        help="Used for topic research. Leave blank to use the server environment key.",
    )
    research_model = st.text_input(
        "Research model",
        value=st.session_state.get("ad_doctor_research_model", "sonar-pro"),
        help="Example: sonar-pro",
    )
    perplexity_endpoint = st.text_input(
        "Perplexity endpoint",
        value=st.session_state.get(
            "ad_doctor_perplexity_endpoint",
            "https://api.perplexity.ai/v1/sonar",
        ),
    )
    st.divider()
    openrouter_key = st.text_input(
        "OpenRouter API key",
        value=st.session_state.get("ad_doctor_openrouter_api_key", ""),
        type="password",
        help="Stored for this browser session. The current content writer is still the local Ad Doctor template engine.",
    )
    writer_model = st.text_input(
        "Writing model",
        value=st.session_state.get(
            "ad_doctor_writer_model",
            os.getenv("OPENROUTER_MODEL", "local-ad-doctor-template") or "local-ad-doctor-template",
        ),
        help="The local template engine uses no AI credits. OpenRouter support can be wired into the writing step next.",
    )
    action_columns = st.columns([1, 1, 2])
    with action_columns[0]:
        if st.button("Save model setup", type="primary"):
            st.session_state["ad_doctor_research_provider"] = research_provider
            st.session_state["ad_doctor_perplexity_api_key"] = perplexity_key.strip()
            st.session_state["ad_doctor_research_model"] = research_model.strip() or "sonar-pro"
            st.session_state["ad_doctor_perplexity_endpoint"] = (
                perplexity_endpoint.strip() or "https://api.perplexity.ai/v1/sonar"
            )
            st.session_state["ad_doctor_openrouter_api_key"] = openrouter_key.strip()
            st.session_state["ad_doctor_writer_model"] = writer_model.strip() or "local-ad-doctor-template"
            apply_runtime_provider_settings()
            st.success("Model setup saved for this session.")
    with action_columns[1]:
        if st.button("Clear session keys"):
            for key in [
                "ad_doctor_perplexity_api_key",
                "ad_doctor_openrouter_api_key",
            ]:
                st.session_state.pop(key, None)
            apply_runtime_provider_settings()
            st.info("Session keys cleared.")

    st.markdown(
        """
        <div class="ad-section-note">
        Fetching articles uses the source scraper, not an AI model. Research uses Perplexity.
        Draft writing currently uses the local Ad Doctor content framework, so it consumes no AI credits.
        </div>
        """,
        unsafe_allow_html=True,
    )


def apply_runtime_provider_settings() -> None:
    api_key = st.session_state.get("ad_doctor_perplexity_api_key")
    model = st.session_state.get("ad_doctor_research_model")
    endpoint = st.session_state.get("ad_doctor_perplexity_endpoint")
    perplexity_research.PERPLEXITY_API_KEY = (
        str(api_key).strip() if api_key is not None else os.getenv("PERPLEXITY_API_KEY", "").strip()
    )
    perplexity_research.PERPLEXITY_MODEL = (
        str(model).strip() if model else os.getenv("PERPLEXITY_MODEL", "sonar-pro").strip() or "sonar-pro"
    )
    perplexity_research.PERPLEXITY_ENDPOINT = (
        str(endpoint).strip()
        if endpoint
        else os.getenv("PERPLEXITY_ENDPOINT", "https://api.perplexity.ai/v1/sonar").strip()
        or "https://api.perplexity.ai/v1/sonar"
    )


def render_source_input() -> None:
    st.write("Import approved advertising/news sources from an XLS/XLSX/CSV file or a Google Sheet CSV export.")

    expected_columns = ", ".join(SOURCE_COLUMNS)
    st.caption(f"Expected columns: {expected_columns}")

    upload_column, sheet_column = st.columns(2)

    with upload_column:
        st.markdown("#### Local file")
        uploaded_file = st.file_uploader(
            "Upload source list",
            type=["xls", "xlsx", "csv"],
            label_visibility="collapsed",
        )
        if uploaded_file and st.button("Import uploaded file", type="primary"):
            try:
                result = import_sources_from_file(uploaded_file, filename=uploaded_file.name)
                render_import_result(result)
            except Exception as exc:
                st.error(str(exc))

        sample_source_path = DATA_DIR / "input" / "sample_sources.csv"
        if sample_source_path.exists() and st.button("Import sample CSV"):
            try:
                result = import_sources_from_file(sample_source_path)
                render_import_result(result)
            except Exception as exc:
                st.error(str(exc))

    with sheet_column:
        st.markdown("#### Google Sheet")
        sheet_url = st.text_input("Google Sheet URL or CSV export URL")
        if sheet_url:
            try:
                st.caption(f"CSV source: {google_sheet_url_to_csv_url(sheet_url)}")
            except ValueError as exc:
                st.warning(str(exc))
        if st.button("Import Google Sheet"):
            try:
                result = import_sources_from_google_sheet(sheet_url)
                render_import_result(result)
            except Exception as exc:
                st.error(str(exc))

    st.divider()
    st.markdown("#### Current approved sources")
    with closing(get_connection()) as connection:
        sources = list_sources(connection)

    if not sources:
        st.info("No sources imported yet.")
        return

    active_count = sum(1 for source in sources if source["active_status"] == 1)
    inactive_count = len(sources) - active_count
    metric_columns = st.columns(3)
    metric_columns[0].metric("Total sources", len(sources))
    metric_columns[1].metric("Active sources", active_count)
    metric_columns[2].metric("Inactive sources", inactive_count)
    st.dataframe(sources, width="stretch", hide_index=True)


def render_import_result(result) -> None:
    st.success(
        f"Imported {result.valid_rows} valid source row(s): "
        f"{result.inserted} inserted, {result.updated} updated, {result.skipped_rows} skipped."
    )
    for warning in result.warnings:
        st.warning(warning)


def render_article_fetch_status() -> None:
    st.write(
        "Fetch all discoverable article candidates only from active approved sources. "
        "RSS/feed discovery is used first, then homepage/category crawl, then sitemap fallback."
    )
    st.caption("Blocked, rate-limited, or unreachable pages are logged instead of guessed.")
    if st.button("Fetch approved-source articles", type="primary"):
        with closing(get_connection()) as connection:
            total_sources = len(list_active_sources(connection))
        progress_bar = st.progress(0)
        current_status = st.empty()
        recent_events = st.empty()
        progress_state = {"completed_sources": 0, "events": []}

        def update_fetch_progress(event: dict[str, object]) -> None:
            completed = int(event.get("completed_sources") or progress_state["completed_sources"])
            progress_state["completed_sources"] = completed
            progress_bar.progress(min(completed / max(total_sources, 1), 1.0))
            current_status.write(str(event.get("message") or "Fetching..."))
            progress_state["events"].append(str(event.get("message") or ""))
            recent_events.code("\n".join(progress_state["events"][-8:]))

        with st.spinner("Fetching articles from approved sources..."):
            try:
                summary = fetch_articles(
                    max_articles_per_source=None,
                    progress_callback=update_fetch_progress,
                    exhaustive_scan=True,
                    source_time_budget_seconds=180,
                )
                st.success(
                    f"Fetch complete: {summary.inserted} inserted, {summary.updated} updated, "
                    f"{summary.failed} failed in {summary.duration_seconds}s."
                )
                if summary.messages:
                    with st.expander("Fetch messages"):
                        for message in summary.messages:
                            st.write(message)
            except Exception as exc:
                st.error(str(exc))

    with closing(get_connection()) as connection:
        logs = list_run_logs(connection, phase="phase_3_article_fetch", limit=50)
    if logs:
        st.dataframe(logs, width="stretch", hide_index=True)
    else:
        st.info("No article fetch run has been logged yet.")


def render_extracted_articles() -> None:
    with closing(get_connection()) as connection:
        total_articles = count_articles(connection)
        source_counts = list_article_source_counts(connection)

    if total_articles == 0:
        st.info("No articles stored yet. Run the Article Fetch Status step first.")
        return

    st.caption("Article storage by source. This prevents the newest source from making the table look one-source only.")
    st.dataframe(source_counts, width="stretch", hide_index=True)

    source_options = ["All sources"] + [row["source_name"] for row in source_counts]
    selected_source = st.selectbox("Source filter", source_options)
    source_filter = None if selected_source == "All sources" else selected_source

    with closing(get_connection()) as connection:
        articles = list_articles_by_source(connection, source_name=source_filter, limit=None)

    active_source_count = len([row for row in source_counts if row["article_count"]])
    metric_columns = st.columns(3)
    metric_columns[0].metric("Stored articles", total_articles)
    metric_columns[1].metric("Sources with stored articles", active_source_count)
    metric_columns[2].metric("Visible rows", len(articles))
    st.caption(
        f"Showing all {len(articles)} stored article row(s)"
        + (f" from {selected_source}." if source_filter else " across all sources.")
    )
    display_rows = []
    for article in articles:
        display_rows.append(
            {
                "source_name": article["source_name"],
                "headline": article["headline"],
                "article_url": article["article_url"],
                "published_date": article["published_date"],
                "author": article["author"],
                "has_text": bool(article["article_text"]),
                "fetch_run_id": article.get("fetch_run_id"),
                "fetch_timestamp": article["fetch_timestamp"],
            }
        )
    st.dataframe(display_rows, width="stretch", hide_index=True)


def render_extracted_topics() -> None:
    st.write("Extract exact news-event topics from stored article headlines and link each topic to source coverage.")
    st.caption("Extraction always uses all stored approved-source articles.")
    if st.button("Extract exact topics", type="primary"):
        with st.spinner("Extracting exact topics..."):
            try:
                summary = extract_topics(
                    article_fetch_run_id=None,
                    article_limit=None,
                )
                st.success(
                    f"Topic extraction complete: {summary.linked_coverages} coverage link(s), "
                    f"{summary.skipped_articles} skipped article(s)."
                )
            except Exception as exc:
                st.error(str(exc))

    with closing(get_connection()) as connection:
        total_topics = count_topics(connection)
        topics = list_topics(connection, limit=None)
        logs = list_run_logs(connection, phase="phase_4_topic_extraction", limit=25)

    if topics:
        st.metric("Extracted topics", total_topics)
        st.caption(f"Showing all {len(topics)} extracted topic row(s).")
        display_topics = [
            {
                "topic": topic["exact_topic"],
                "status": topic["status"],
                "unique_sources": topic["source_coverage_count"],
                "article_count": topic["article_count"],
                "normalized_topic": topic["normalized_topic"],
            }
            for topic in topics
        ]
        st.dataframe(display_topics, width="stretch", hide_index=True)
    else:
        st.info("No topics extracted yet.")

    if logs:
        with st.expander("Topic extraction logs"):
            st.dataframe(logs, width="stretch", hide_index=True)


def render_qualified_topics() -> None:
    st.write("Qualify topics only when the same news event appears on at least 3 unique approved source websites.")
    action_columns = st.columns(3)
    with action_columns[0]:
        if st.button("Apply 3-site qualification", type="primary"):
            with st.spinner("Qualifying topics..."):
                try:
                    summary = qualify_topics()
                    st.success(
                        f"Qualification complete: {summary.qualified_topics} qualified, "
                        f"{summary.rejected_topics} rejected. Gate: {summary.minimum_required_sources} source(s)."
                    )
                except Exception as exc:
                    st.error(str(exc))
    with action_columns[1]:
        if st.button("Expand approved-source coverage"):
            with st.spinner("Matching every topic across all stored approved-source articles..."):
                try:
                    expansion_summary = expand_topic_coverage_from_stored_articles(
                        topic_limit=None,
                    )
                    qualification_summary = qualify_topics()
                    st.success(
                        f"Coverage expansion checked {expansion_summary.topics_checked} topic(s) against "
                        f"{expansion_summary.articles_checked} stored article(s), linked "
                        f"{expansion_summary.links_added} same-event coverage item(s), then re-scored: "
                        f"{qualification_summary.qualified_topics} qualified."
                    )
                except Exception as exc:
                    st.error(str(exc))
    with action_columns[2]:
        if st.button("Prioritize top topics"):
            try:
                summary = prioritize_topics()
                st.success(
                    f"Shortlisted {summary.shortlisted_topics} of {summary.qualified_topics} qualified topic(s)."
                )
            except Exception as exc:
                st.error(str(exc))

    with closing(get_connection()) as connection:
        qualified_topics = list_topic_scores(connection, qualified_only=True)

    if qualified_topics:
        st.caption(f"Showing all {len(qualified_topics)} qualified topic(s).")
        st.dataframe(build_score_display_rows(qualified_topics), width="stretch", hide_index=True)
    else:
        st.info("No qualified topics yet.")


def render_topic_score_breakdown() -> None:
    with closing(get_connection()) as connection:
        topic_scores = list_topic_scores(connection)

    if not topic_scores:
        st.info("No topic scores yet. Apply qualification first.")
        return

    st.caption("Current gate: at least 3 unique approved source websites must cover the same news event.")
    st.dataframe(build_score_display_rows(topic_scores), width="stretch", hide_index=True)


def render_locked_topics() -> None:
    st.write("Lock every currently qualified topic for a run before research and content generation.")
    with closing(get_connection()) as connection:
        qualified_count = len(list_topic_scores(connection, qualified_only=True))
    st.metric("Qualified topics available to lock", qualified_count)
    if st.button("Lock all qualified topics", type="primary"):
        try:
            if qualified_count == 0:
                st.warning("No qualified topics are available to lock yet.")
            else:
                summary = lock_top_topics(limit=qualified_count)
                st.success(f"Locked {summary.locked_count} topic(s) for run {summary.run_id}.")
        except Exception as exc:
            st.error(str(exc))

    with closing(get_connection()) as connection:
        latest_locked_run_id = get_latest_locked_run_id(connection)
        locked = (
            list_locked_topics(connection, run_id=latest_locked_run_id)
            if latest_locked_run_id
            else []
        )

    if locked:
        st.caption(f"Showing latest locked run: {latest_locked_run_id}")
        st.dataframe(locked, width="stretch", hide_index=True)
    else:
        st.info("No topics locked yet.")


def render_research_dossier() -> None:
    st.write("Build cited research dossiers for locked topics from stored article coverage.")
    if st.button("Build research dossiers", type="primary"):
        try:
            summary = build_research_dossiers()
            st.success(f"Built {summary.dossiers_created} research dossier(s).")
        except Exception as exc:
            st.error(str(exc))

    with closing(get_connection()) as connection:
        latest_locked_run_id = get_latest_locked_run_id(connection)
        dossiers = (
            list_research_dossiers(connection, run_id=latest_locked_run_id)
            if latest_locked_run_id
            else []
        )

    if not dossiers:
        st.info("No research dossiers yet.")
        return

    st.caption(f"Showing dossiers for latest locked run: {latest_locked_run_id}")
    for dossier in dossiers:
        with st.expander(dossier["locked_topic"]):
            st.code(dossier["dossier_json"], language="json")


def render_campaign_dissection() -> None:
    st.write("Generate campaign dissections using the configurable roadmap template.")
    if st.button("Generate campaign dissections", type="primary"):
        try:
            summary = run_campaign_dissections()
            st.success(f"Generated {summary.dissections_created} campaign dissection(s).")
        except Exception as exc:
            st.error(str(exc))

    with closing(get_connection()) as connection:
        latest_locked_run_id = get_latest_locked_run_id(connection)
        outputs = (
            list_dissection_outputs(connection, run_id=latest_locked_run_id)
            if latest_locked_run_id
            else []
        )

    if not outputs:
        st.info("No campaign dissections yet.")
        return

    st.caption(f"Showing dissections for latest locked run: {latest_locked_run_id}")
    for output in outputs:
        with st.expander(output["locked_topic"]):
            st.markdown(output["output_markdown"])


def render_content_drafts(content_type: str, show_generate_button: bool = True) -> None:
    label = "blog" if content_type == "blog" else "social media"
    if show_generate_button:
        st.write(f"Generate and review {label} drafts. Drafts stay pending until approval.")
    if show_generate_button and st.button(f"Generate {label} drafts", type="primary"):
        try:
            apply_runtime_provider_settings()
            summary = generate_content_outputs()
            st.success(
                f"Generated {summary.blog_drafts_created} blog draft(s) and "
                f"{summary.social_drafts_created} social draft(s)."
            )
        except Exception as exc:
            st.error(str(exc))

    with closing(get_connection()) as connection:
        latest_locked_run_id = get_latest_locked_run_id(connection)
        outputs = (
            list_content_outputs(
                connection,
                content_type=content_type,
                run_id=latest_locked_run_id,
            )
            if latest_locked_run_id
            else []
        )

    if not outputs:
        st.info(f"No {label} drafts yet.")
        return

    rejected_outputs = [output for output in outputs if output["approval_status"] == "rejected"]
    outputs = [output for output in outputs if output["approval_status"] != "rejected"]
    if rejected_outputs:
        st.caption(
            f"{len(rejected_outputs)} {label} item(s) are hidden here because they failed the exact "
            "3-source reference rule."
        )
    if not outputs:
        st.info(f"No publishable {label} drafts for the latest locked run.")
        return

    latest_created_at = max(str(output["created_at"]) for output in outputs)
    outputs = [output for output in outputs if str(output["created_at"]) == latest_created_at]
    st.caption(
        f"Showing latest generated {label} batch for locked run: {latest_locked_run_id}. "
        f"{len(outputs)} draft(s), generated at {latest_created_at}."
    )
    for output in outputs:
        label_text = (
            f"#{output['id']} | {output['locked_topic']} "
            f"({output['approval_status']}, {output['created_at']})"
        )
        with st.expander(label_text):
            if content_type == "social":
                render_social_draft(output["draft_markdown"])
            else:
                st.markdown(output["draft_markdown"])


def render_social_draft(draft_markdown: str) -> None:
    structured_draft = parse_structured_social_draft(draft_markdown)
    if structured_draft:
        render_structured_social_draft(structured_draft)
        return

    intro, sections, references = parse_social_draft_sections(draft_markdown)
    if not sections:
        st.markdown(draft_markdown)
        return

    st.markdown(intro)
    overview_tab, carousel_tab, publishing_tab, references_tab = st.tabs(
        ["Overview", "Carousel", "Publishing", "References"]
    )

    with overview_tab:
        for section_number in range(1, 10):
            section = sections.get(section_number)
            if section:
                st.markdown(f"#### SECTION {section_number}: {section['title']}")
                st.markdown(section["body"])

    with carousel_tab:
        section = sections.get(10)
        if section:
            st.markdown(f"#### SECTION 10: {section['title']}")
            st.markdown(section["body"])
        else:
            st.info("No carousel section found in this draft.")

    with publishing_tab:
        for section_number in range(11, 17):
            section = sections.get(section_number)
            if section:
                st.markdown(f"#### SECTION {section_number}: {section['title']}")
                st.markdown(section["body"])

    with references_tab:
        st.markdown(references or "No references found in this draft.")


def render_structured_social_draft(draft: dict[str, object]) -> None:
    fields = draft["fields"]
    slides = draft["slides"]
    publishing = draft["publishing"]
    references = str(draft.get("references") or "")

    topic = fields.get("Topic Name", "Social Media Carousel Draft")
    st.markdown(f"### {topic}")
    overview_tab, carousel_tab, publishing_tab, references_tab = st.tabs(
        ["Overview", "Carousel", "Publishing", "References"]
    )

    with overview_tab:
        for label in [
            "Qualification Status",
            "Sources Count",
            "Sources Used",
            "Perplexity Research Summary",
            "Core Strategic Insight",
            "Selected Content Pillar",
        ]:
            value = fields.get(label)
            if value:
                st.markdown(f"**{label}:**")
                st.markdown(str(value))

    with carousel_tab:
        if not slides:
            st.info("No publishable carousel was generated for this draft.")
        for slide in slides:
            headline = slide.get("Headline") or f"Slide {slide.get('number')}"
            with st.expander(f"Slide {slide.get('number')} | {headline}", expanded=int(slide.get("number", 0)) == 1):
                for label in ["Headline", "Body", "Visual Suggestion", "Bold Words", "Source"]:
                    value = slide.get(label)
                    if value:
                        st.markdown(f"**{label}:**")
                        st.markdown(str(value))

    with publishing_tab:
        for label in ["Caption", "Hashtags", "Designer Notes", "Fact-check Notes", "Risk Notes"]:
            value = publishing.get(label)
            if value:
                st.markdown(f"#### {label}")
                st.markdown(str(value))

    with references_tab:
        st.markdown(references or "No references found in this draft.")


def parse_structured_social_draft(draft_markdown: str) -> dict[str, object] | None:
    if "Topic Name:" not in draft_markdown or "Qualification Status:" not in draft_markdown:
        return None

    reference_split = re.split(r"\nSource References:\s*\n", draft_markdown, maxsplit=1)
    body_text = reference_split[0].strip()
    references = reference_split[1].strip() if len(reference_split) > 1 else ""
    if "\nCarousel Draft:" in body_text:
        header_text, remainder = body_text.split("\nCarousel Draft:", 1)
    else:
        header_text, remainder = body_text, ""

    fields = extract_labeled_blocks(
        header_text,
        [
            "Topic Name",
            "Qualification Status",
            "Sources Count",
            "Sources Used",
            "Perplexity Research Summary",
            "Core Strategic Insight",
            "Selected Content Pillar",
            "Decision",
        ],
    )
    if not fields:
        return None

    publishing_start = re.search(r"^Caption:\s*", remainder, flags=re.MULTILINE)
    carousel_text = remainder[: publishing_start.start()].strip() if publishing_start else remainder.strip()
    publishing_text = remainder[publishing_start.start() :].strip() if publishing_start else ""
    slides = parse_structured_slides(carousel_text)
    publishing = extract_labeled_blocks(
        publishing_text,
        ["Caption", "Hashtags", "Designer Notes", "Fact-check Notes", "Risk Notes"],
    )
    return {
        "fields": fields,
        "slides": slides,
        "publishing": publishing,
        "references": references,
    }


def parse_structured_slides(carousel_text: str) -> list[dict[str, object]]:
    pattern = re.compile(r"^Slide (?P<number>\d+):\s*$", flags=re.MULTILINE)
    matches = list(pattern.finditer(carousel_text))
    slides = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(carousel_text)
        slide_fields = extract_labeled_blocks(
            carousel_text[start:end].strip(),
            ["Headline", "Body", "Visual Suggestion", "Bold Words", "Source"],
        )
        slide_fields["number"] = int(match.group("number"))
        slides.append(slide_fields)
    return slides


def extract_labeled_blocks(text: str, labels: list[str]) -> dict[str, str]:
    if not text.strip():
        return {}
    label_pattern = "|".join(re.escape(label) for label in labels)
    pattern = re.compile(
        rf"^(?P<label>{label_pattern}):\s*(?P<first_line>.*)$",
        flags=re.MULTILINE,
    )
    matches = list(pattern.finditer(text))
    blocks: dict[str, str] = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        value = "\n".join(
            part
            for part in [match.group("first_line").strip(), text[start:end].strip()]
            if part
        ).strip()
        blocks[match.group("label")] = value
    return blocks


def parse_social_draft_sections(draft_markdown: str):
    source_split = re.split(r"\n## Source References\s*\n", draft_markdown, maxsplit=1)
    body_text = source_split[0]
    references = source_split[1].strip() if len(source_split) > 1 else ""
    pattern = re.compile(
        r"^## SECTION (?P<number>\d+): (?P<title>.+?)\s*$",
        flags=re.MULTILINE,
    )
    matches = list(pattern.finditer(body_text))
    if not matches:
        return draft_markdown, {}, references

    intro = body_text[: matches[0].start()].strip()
    sections = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body_text)
        sections[int(match.group("number"))] = {
            "title": match.group("title").strip(),
            "body": body_text[start:end].strip(),
        }
    return intro, sections, references


def render_approval_queue() -> None:
    st.write("Manually approve or reject generated drafts. Only approved drafts can be exported.")
    with closing(get_connection()) as connection:
        queue = list_approval_queue(connection)

    if not queue:
        st.info("No generated drafts are waiting for approval.")
        return

    options = {
        f"{item['content_output_id']} | {item['content_type']} | {item['locked_topic']} | {item['approval_status']}": item
        for item in queue
    }
    selected_label = st.selectbox("Draft", list(options.keys()))
    selected = options[selected_label]
    st.markdown(selected["draft_markdown"])
    reviewer_notes = st.text_area("Reviewer notes")

    action_columns = st.columns(3)
    with action_columns[0]:
        if st.button("Approve selected", type="primary"):
            summary = set_content_approval(
                int(selected["content_output_id"]),
                "approved",
                reviewer_notes=reviewer_notes,
            )
            st.success(f"Content output {summary.content_output_id} approved.")
    with action_columns[1]:
        if st.button("Reject selected"):
            summary = set_content_approval(
                int(selected["content_output_id"]),
                "rejected",
                reviewer_notes=reviewer_notes,
            )
            st.warning(f"Content output {summary.content_output_id} rejected.")
    with action_columns[2]:
        if st.button("Reset to pending"):
            summary = set_content_approval(
                int(selected["content_output_id"]),
                "pending",
                reviewer_notes=reviewer_notes,
            )
            st.info(f"Content output {summary.content_output_id} reset to pending.")


def render_export_options() -> None:
    st.write("Export approved drafts only. Pending and rejected drafts are intentionally excluded.")
    if st.button("Export approved drafts", type="primary"):
        try:
            summary = export_approved_outputs()
            st.success(f"Exported {summary.approved_outputs} approved draft(s).")
            for file_path in summary.files:
                st.write(file_path)
        except Exception as exc:
            st.error(str(exc))

    with closing(get_connection()) as connection:
        logs = list_run_logs(connection, phase="phase_10_export", limit=25)

    if logs:
        st.dataframe(logs, width="stretch", hide_index=True)
    else:
        st.info("No exports have been run yet.")


def render_automation_alerts(
    *,
    show_recipients: bool = True,
    show_smtp_status: bool = True,
) -> None:
    settings = get_automation_settings()
    st.write("Choose when Ad Doctor should run the approved-source scan.")
    scheduler_status = get_app_scheduler_status()
    if scheduler_status.enabled_by_env and scheduler_status.running:
        st.success("Scheduler is active. Saved run times will trigger automatically while this app is running.")
    elif scheduler_status.enabled_by_env:
        st.warning(scheduler_status.message)
    else:
        st.warning("Scheduler is disabled. Set AD_DOCTOR_APP_SCHEDULER_ENABLED=1 only if you intentionally turned it off.")

    schedule_times = settings.schedule_times or (settings.schedule_time,)
    settings_columns = st.columns([1, 2, 1, 1])
    with settings_columns[0]:
        enabled = st.checkbox("Auto-run enabled", value=settings.enabled)
    with settings_columns[1]:
        schedule_values_text = st.text_area(
            "Daily run times",
            value="\n".join(schedule_times),
            height=96,
            help="Use one time per line, or separate times with commas. Example: 09:00, 13:30, 18:00",
            placeholder="09:00\n13:30\n18:00",
        )
        try:
            parsed_schedule_times = parse_schedule_times(schedule_values_text)
            st.caption(f"Will run daily at: {', '.join(parsed_schedule_times)}")
        except Exception:
            st.caption("Enter times like 09:00, 13:30, 18:00")
    with settings_columns[2]:
        notify_success = st.checkbox("Mail when done", value=settings.notify_on_success)
    with settings_columns[3]:
        notify_failure = st.checkbox("Mail on failure", value=settings.notify_on_failure)

    budget = st.number_input(
        "Seconds per source",
        min_value=60,
        max_value=600,
        value=settings.fetch_source_time_budget_seconds,
        step=30,
    )

    if st.button("Save automation settings", type="primary"):
        try:
            normalized_schedule_times = parse_schedule_times(schedule_values_text)
            saved = save_automation_settings(
                enabled=enabled,
                schedule_time=normalized_schedule_times[0],
                schedule_times=normalized_schedule_times,
                notify_on_success=notify_success,
                notify_on_failure=notify_failure,
                fetch_source_time_budget_seconds=int(budget),
            )
            scheduler_result = install_or_update_daily_task(saved)
            if scheduler_result.success:
                st.success(scheduler_result.message)
            elif scheduler_result.supported:
                st.warning(scheduler_result.message)
            else:
                st.info(
                    "Settings saved. The app scheduler will use these run times while the Streamlit service is running."
                )
        except Exception as exc:
            st.error(str(exc))

    st.caption(
        "On Render, the app scheduler checks these times inside the running Streamlit service. "
        "On Windows, saving can also create local Scheduled Tasks."
    )
    history = list_automation_run_history(limit=25)
    if history:
        st.markdown("#### Recent scheduled runs")
        st.dataframe(history, width="stretch", hide_index=True)
    else:
        st.info("No scheduled auto-runs have been recorded yet.")

    if not show_recipients and not show_smtp_status:
        return

    st.divider()
    render_email_alert_settings(show_smtp_status=show_smtp_status)


def render_email_alert_settings(*, show_smtp_status: bool = True) -> None:
    st.markdown("#### Content-ready emails")
    recipient_columns = st.columns([2, 2, 1])
    with recipient_columns[0]:
        recipient_email = st.text_input("User email")
    with recipient_columns[1]:
        recipient_name = st.text_input("Display name")
    with recipient_columns[2]:
        recipient_enabled = st.checkbox("Enabled", value=True)
    if st.button("Add or update user"):
        try:
            upsert_app_user(
                recipient_email,
                display_name=recipient_name or None,
                notifications_enabled=recipient_enabled,
            )
            st.success("User saved.")
        except Exception as exc:
            st.error(str(exc))

    users = list_app_users()
    if users:
        st.dataframe(
            [
                {
                    "email": user["email"],
                    "display_name": user["display_name"],
                    "notifications_enabled": bool(user["notifications_enabled"]),
                    "last_login_at": user["last_login_at"],
                }
                for user in users
            ],
            width="stretch",
            hide_index=True,
        )
    else:
        st.info("No notification users saved yet.")

    if show_smtp_status:
        st.divider()
        st.markdown("#### Sender email setup")
        smtp_settings = load_smtp_settings()
        with st.form("smtp_settings_form"):
            smtp_columns = st.columns([2, 1])
            with smtp_columns[0]:
                smtp_host = st.text_input("SMTP host", value=smtp_settings.host or "")
            with smtp_columns[1]:
                smtp_port = st.number_input(
                    "SMTP port",
                    min_value=1,
                    max_value=65535,
                    value=int(smtp_settings.port or 587),
                    step=1,
                )
            smtp_username = st.text_input("SMTP username", value=smtp_settings.username or "")
            smtp_password = st.text_input(
                "SMTP password or app password",
                value="",
                type="password",
                help="Leave blank to keep the existing saved password.",
            )
            smtp_from = st.text_input("Sender email", value=smtp_settings.from_email or "")
            smtp_use_tls = st.checkbox("Use TLS", value=smtp_settings.use_tls)
            submitted_smtp = st.form_submit_button("Save email sender settings", type="primary")
            if submitted_smtp:
                try:
                    saved_smtp = save_smtp_settings(
                        smtp_host=smtp_host,
                        smtp_port=int(smtp_port),
                        smtp_username=smtp_username or None,
                        smtp_password=smtp_password or None,
                        smtp_from=smtp_from,
                        use_tls=smtp_use_tls,
                    )
                    if saved_smtp.is_ready:
                        st.success("Email sender settings saved.")
                    else:
                        st.warning(f"Saved, but still missing: {', '.join(saved_smtp.missing_fields)}")
                except Exception as exc:
                    st.error(str(exc))

        smtp_settings = load_smtp_settings()
        st.markdown("#### Mail delivery status")
        if smtp_settings.is_ready:
            st.success("Email sending is configured.")
            password_status = "password available" if smtp_settings.password else "no password saved"
            st.caption(f"Sender: {smtp_settings.from_email} | Source: {smtp_settings.source} | {password_status}")
        else:
            st.warning(f"Email sending needs: {', '.join(smtp_settings.missing_fields)}")
            st.caption(
                "Save SMTP host and sender email above. Passwords are stored in the app database, not in code."
            )

        test_email = st.text_input(
            "Send test email to",
            value=st.session_state.get("ad_doctor_user_email", ""),
            key="email_test_recipient",
        )
        if st.button("Send test email"):
            try:
                summary = send_test_notification_email(test_email)
                if summary.sent:
                    st.success("Test email sent.")
                elif summary.skipped_reason:
                    st.warning(summary.skipped_reason)
                else:
                    st.error("Test email failed. Check the notification events below.")
            except Exception as exc:
                st.error(str(exc))

        events = list_notification_events(limit=50)
        if events:
            st.dataframe(events, width="stretch", hide_index=True)
        else:
            st.info("No notification events yet.")


def parse_schedule_time(value: str) -> tuple[int, int]:
    try:
        hour_text, minute_text = value.split(":", 1)
        return int(hour_text), int(minute_text)
    except Exception:
        return 9, 0


def build_score_display_rows(topic_scores):
    rows = []
    for topic in topic_scores:
        required = int(topic["minimum_required_sources"])
        coverage = int(topic["unique_source_coverage_count"])
        rows.append(
            {
                "topic": topic["exact_topic"],
                "status": topic["status"],
                "unique_sources": coverage,
                "required_sources": required,
                "coverage_gap": max(0, required - coverage),
                "coverage_percent": round(float(topic["coverage_percentage"]) * 100, 2),
                "credibility_score": topic["credibility_score"],
                "freshness_score": topic["freshness_score"],
                "data_richness_score": topic["data_richness_score"],
                "priority_boost": bool(topic["priority_boost_applied"]),
                "total_score": topic["total_score"],
            }
        )
    return rows


if __name__ == "__main__":
    render_dashboard_shell()
