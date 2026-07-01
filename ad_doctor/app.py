from __future__ import annotations

from contextlib import closing
from datetime import UTC, datetime, timedelta
from html import escape
import json
import os
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
from src.config import APP_NAME
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
from src.pipeline_runner import generate_content_from_qualified_topics, run_real_pipeline
from src.prioritization_engine import prioritize_topics
from src.qualification_engine import qualify_topics
from src.research_engine import build_research_dossiers
from src.settings_engine import (
    DEFAULT_CONTENT_FRAMEWORK,
    get_research_engine_by_key,
    get_research_engine_by_label,
    get_research_engine_labels,
    load_content_framework_settings,
    load_research_settings,
    parse_carousel_structure,
    parse_content_framework_prompt,
    parse_json_list,
    reset_content_framework_settings,
    save_content_framework_settings,
    save_research_settings,
    serialize_content_framework_prompt,
)
from src.source_manager import (
    SOURCE_COLUMNS,
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
    "Rejected Topics",
    "Qualified Topics",
    "Topic Score Breakdown",
    "Locked Topics",
    "Research Dossier",
    "Campaign Dissection",
]

MAIN_PAGES = [
    "Dashboard",
    "Content Studio",
    "Control Room",
    "Arsenal",
]


def render_dashboard_shell() -> None:
    st.set_page_config(page_title=APP_NAME, layout="wide")
    initialize_database()
    apply_runtime_provider_settings()
    start_app_scheduler()
    inject_app_styles()

    with st.sidebar:
        st.markdown(
            """
            <div class="ad-brand-lockup">
                <div class="ad-brand-mark">AD</div>
                <div>
                    <div class="ad-brand-name">Ad Doctor</div>
                    <div class="ad-brand-caption">Content intelligence</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        page = st.radio("Workspace", MAIN_PAGES, label_visibility="collapsed")
        st.markdown('<div class="ad-sidebar-foot">3-source qualification is always enforced.</div>', unsafe_allow_html=True)

    render_app_header(page)

    if page == "Dashboard":
        render_dashboard()
    elif page == "Content Studio":
        render_content_studio()
    elif page == "Control Room":
        render_control_room()
    elif page == "Arsenal":
        render_arsenal()


def inject_app_styles() -> None:
    st.markdown(
        """
        <style>
        :root {
            --ad-bg: #f0f0ed;
            --ad-panel: #ffffff;
            --ad-card: #f8f8f5;
            --ad-card-strong: #f1f1ed;
            --ad-line: #deded8;
            --ad-text: #111210;
            --ad-muted: #6e706a;
            --ad-accent: #b7ff4a;
            --ad-accent-strong: #8fe02c;
            --ad-success: #1e8a4b;
            --ad-warning: #a26500;
            --ad-danger: #bd3535;
        }

        .stApp {
            background: var(--ad-bg) !important;
            color: var(--ad-text);
        }

        [data-testid="stAppViewContainer"] > .main {
            background: transparent !important;
        }

        .block-container {
            max-width: 1440px;
            padding: 2rem 2.5rem 4rem !important;
        }

        [data-testid="stSidebar"] {
            background: var(--ad-panel) !important;
            border-right: 1px solid var(--ad-line);
        }

        [data-testid="stSidebar"] > div:first-child {
            padding-top: 1.4rem;
        }

        [data-testid="stSidebar"] * {
            color: var(--ad-text) !important;
        }

        [data-testid="stCaptionContainer"],
        .ad-muted {
            color: var(--ad-muted) !important;
            font-size: .88rem;
        }

        .ad-brand-lockup {
            display: flex;
            align-items: center;
            gap: .7rem;
            padding: .3rem .15rem 1.7rem;
        }

        .ad-brand-mark {
            display: grid;
            place-items: center;
            width: 2.25rem;
            height: 2.25rem;
            border-radius: 50%;
            background: var(--ad-text);
            color: #fff !important;
            font-size: .7rem;
            font-weight: 800;
        }

        .ad-brand-name {
            font-size: 1rem;
            font-weight: 750;
            line-height: 1.15;
        }

        .ad-brand-caption {
            color: var(--ad-muted) !important;
            font-size: .76rem;
            margin-top: .12rem;
        }

        [data-testid="stSidebar"] .stRadio > div {
            gap: .24rem;
        }

        [data-testid="stSidebar"] label[data-baseweb="radio"] {
            min-height: 2.65rem;
            padding: .55rem .72rem;
            border-radius: 8px;
            border: 1px solid transparent;
            transition: background .15s ease, border-color .15s ease;
        }

        [data-testid="stSidebar"] label[data-baseweb="radio"] > div:first-child {
            display: none;
        }

        [data-testid="stSidebar"] label[data-baseweb="radio"]:hover {
            background: var(--ad-card);
            border-color: var(--ad-line);
        }

        [data-testid="stSidebar"] label[data-baseweb="radio"]:has(input:checked) {
            background: var(--ad-text);
            border-color: var(--ad-text);
        }

        [data-testid="stSidebar"] label[data-baseweb="radio"]:has(input:checked) p {
            color: #fff !important;
            font-weight: 650;
        }

        .ad-sidebar-foot {
            color: var(--ad-muted) !important;
            font-size: .76rem;
            line-height: 1.45;
            padding: 1.5rem .75rem 0;
        }

        h1, h2, h3, h4, h5, h6,
        .stMarkdown, .stText, p, label, span {
            color: var(--ad-text);
            letter-spacing: 0 !important;
        }

        h1 {
            font-size: 2.1rem !important;
            line-height: 1.05 !important;
        }

        h2, [data-testid="stHeadingWithActionElements"] h2 {
            font-size: 1.55rem !important;
            line-height: 1.18 !important;
            margin-top: 1.3rem !important;
        }

        h3 {
            font-size: 1.15rem !important;
        }

        .ad-product-header {
            display: grid;
            grid-template-columns: minmax(0, 1fr) auto;
            gap: 1rem;
            align-items: center;
            padding: .1rem 0 1.4rem;
            border-bottom: 1px solid var(--ad-line);
            margin-bottom: 1.5rem;
        }

        .ad-kicker {
            color: var(--ad-muted);
            font-size: .72rem;
            text-transform: uppercase;
            margin-bottom: .35rem;
        }

        .ad-product-title {
            font-size: 2rem;
            line-height: 1.05;
            font-weight: 720;
            margin: 0;
        }

        .ad-product-copy {
            max-width: 740px;
            color: var(--ad-muted);
            font-size: .94rem;
            line-height: 1.45;
            margin: .45rem 0 0;
        }

        .ad-header-pills {
            display: flex;
            gap: .4rem;
            flex-wrap: wrap;
            justify-content: flex-end;
        }

        .ad-pill {
            display: inline-flex;
            align-items: center;
            border-radius: 8px;
            padding: .42rem .62rem;
            background: var(--ad-panel);
            border: 1px solid var(--ad-line);
            color: var(--ad-muted);
            font-size: .76rem;
        }

        .ad-status-dot {
            width: .4rem;
            height: .4rem;
            border-radius: 50%;
            background: var(--ad-success);
            margin-right: .38rem;
        }

        .ad-panel,
        .ad-timeline-row,
        .ad-empty-panel {
            border: 1px solid var(--ad-line);
            border-radius: 8px;
            background: var(--ad-card);
        }

        .ad-section-heading {
            display: flex;
            justify-content: space-between;
            align-items: end;
            gap: 1rem;
            margin: 1.8rem 0 .8rem;
        }

        .ad-section-heading h3 {
            margin: 0;
        }

        .ad-section-heading p {
            margin: 0;
            color: var(--ad-muted);
            font-size: .82rem;
        }

        .ad-timeline {
            display: grid;
            gap: .5rem;
        }

        .ad-timeline-row {
            display: grid;
            grid-template-columns: 9.5rem minmax(0, 1fr) auto;
            align-items: center;
            gap: 1rem;
            padding: .8rem .9rem;
            background: var(--ad-panel);
        }

        .ad-time {
            color: var(--ad-muted);
            font-size: .8rem;
        }

        .ad-row-title {
            font-weight: 650;
            font-size: .88rem;
        }

        .ad-row-meta {
            color: var(--ad-muted);
            font-size: .78rem;
            margin-top: .18rem;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }

        .ad-badge {
            display: inline-flex;
            align-items: center;
            min-height: 1.65rem;
            padding: .2rem .5rem;
            border-radius: 999px;
            background: var(--ad-card-strong);
            color: var(--ad-muted);
            font-size: .72rem;
            white-space: nowrap;
        }

        .ad-badge.is-accent {
            background: var(--ad-accent);
            color: #18210c;
        }

        .ad-filter-shell {
            padding: .9rem;
            margin: .25rem 0 1rem;
            border: 1px solid var(--ad-line);
            border-radius: 8px;
            background: var(--ad-card);
        }

        .ad-run-strip {
            display: flex;
            gap: .45rem;
            flex-wrap: wrap;
            padding: .75rem 0 .2rem;
        }

        .ad-empty-panel {
            padding: 2rem;
            color: var(--ad-muted);
            text-align: center;
        }

        div[data-testid="stMetric"] {
            background: var(--ad-card);
            border: 1px solid var(--ad-line);
            border-radius: 8px;
            padding: .9rem .9rem .8rem;
        }

        div[data-testid="stMetric"] label,
        div[data-testid="stMetric"] [data-testid="stMetricLabel"] {
            color: var(--ad-muted) !important;
        }

        div[data-testid="stMetric"] [data-testid="stMetricValue"] {
            color: var(--ad-text) !important;
            font-weight: 650;
            font-size: 1.75rem;
        }

        button[kind="primary"],
        .stButton > button[kind="primary"],
        .stFormSubmitButton > button[kind="primary"] {
            background: var(--ad-text) !important;
            border: 1px solid var(--ad-text) !important;
            color: #fff !important;
            border-radius: 8px !important;
            padding: .65rem .9rem !important;
        }

        button[kind="primary"] *,
        .stButton > button[kind="primary"] *,
        .stFormSubmitButton > button[kind="primary"] * {
            color: #fff !important;
        }

        button[kind="primary"] svg,
        .stButton > button[kind="primary"] svg,
        .stFormSubmitButton > button[kind="primary"] svg {
            color: #fff !important;
            fill: #fff !important;
        }

        .stButton > button,
        .stDownloadButton > button,
        .stFormSubmitButton > button {
            border-radius: 8px !important;
            border: 1px solid var(--ad-line) !important;
            background: var(--ad-panel) !important;
            color: var(--ad-text) !important;
            min-height: 2.65rem;
        }

        .stButton > button:hover,
        .stDownloadButton > button:hover,
        .stFormSubmitButton > button:hover {
            border-color: #b9bbb4 !important;
        }

        [data-baseweb="tab-list"] {
            gap: 1.2rem;
            border-bottom: 1px solid var(--ad-line);
            padding-bottom: 0;
        }

        [data-baseweb="tab"] {
            border-radius: 0 !important;
            color: var(--ad-muted) !important;
            background: transparent !important;
            padding: .65rem .05rem !important;
        }

        [data-baseweb="tab"][aria-selected="true"] {
            color: var(--ad-text) !important;
            font-weight: 650;
        }

        [data-baseweb="tab-highlight"] {
            background-color: var(--ad-text) !important;
        }

        input, textarea, [data-baseweb="select"] > div {
            background: var(--ad-panel) !important;
            border-color: var(--ad-line) !important;
            border-radius: 8px !important;
            color: var(--ad-text) !important;
        }

        [data-baseweb="select"] svg,
        [data-baseweb="select"] [role="button"] svg {
            color: var(--ad-text) !important;
            fill: var(--ad-text) !important;
            opacity: 1 !important;
        }

        [data-baseweb="tag"] {
            background: var(--ad-accent) !important;
        }

        [data-baseweb="tag"] * {
            color: var(--ad-text) !important;
        }

        [data-baseweb="tag"] svg {
            color: var(--ad-text) !important;
            fill: var(--ad-text) !important;
        }

        [data-baseweb="popover"] [role="listbox"],
        [data-baseweb="popover"] ul {
            background: var(--ad-panel) !important;
            border: 1px solid var(--ad-line) !important;
            color: var(--ad-text) !important;
        }

        [data-baseweb="popover"] [role="option"] {
            background: var(--ad-panel) !important;
            color: var(--ad-text) !important;
        }

        [data-baseweb="popover"] [role="option"]:hover,
        [data-baseweb="popover"] [aria-selected="true"] {
            background: var(--ad-card-strong) !important;
        }

        [data-baseweb="popover"] [role="option"] * {
            color: var(--ad-text) !important;
        }

        textarea {
            line-height: 1.5 !important;
        }

        [data-baseweb="checkbox"] div {
            border-radius: 4px !important;
        }

        [data-testid="stExpander"] {
            background: var(--ad-panel);
            border: 1px solid var(--ad-line);
            border-radius: 8px;
            overflow: hidden;
        }

        [data-testid="stExpander"] details {
            border: 0 !important;
        }

        [data-testid="stDataFrame"],
        [data-testid="stTable"] {
            border-radius: 8px;
            overflow: hidden;
            border: 1px solid var(--ad-line);
        }

        .stAlert {
            border-radius: 8px !important;
            border: 1px solid var(--ad-line) !important;
        }

        hr {
            border-color: var(--ad-line) !important;
        }

        @media (max-width: 900px) {
            .block-container {
                padding: 1.1rem 1rem 3rem !important;
            }

            .ad-product-header {
                grid-template-columns: 1fr;
                align-items: start;
            }

            .ad-header-pills {
                justify-content: flex-start;
            }

            .ad-timeline-row {
                grid-template-columns: 1fr auto;
            }

            .ad-time {
                grid-column: 1 / -1;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_app_header(page: str) -> None:
    page_descriptions = {
        "Dashboard": "A simple view of what Ad Doctor found, qualified, and created.",
        "Content Studio": "Find, review, approve, and export social and blog drafts by date.",
        "Control Room": "Manage schedules, sources, research, content instructions, and alerts.",
        "Arsenal": "Technical evidence, pipeline diagnostics, rejected topics, and research detail.",
    }
    scheduler_status = get_app_scheduler_status()
    scheduler_label = "Automation active" if scheduler_status.running else "Automation paused"
    st.markdown(
        f"""
        <div class="ad-product-header">
            <div>
                <div class="ad-kicker">Ad Doctor / {escape(page)}</div>
                <div class="ad-product-title">{escape(page)}</div>
                <div class="ad-product-copy">{page_descriptions.get(page, "Diagnose marketing stories and prepare content.")}</div>
            </div>
            <div class="ad-header-pills">
                <span class="ad-pill"><span class="ad-status-dot"></span>{scheduler_label}</span>
                <span class="ad-pill">3-source rule</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_dashboard() -> None:
    with closing(get_connection()) as connection:
        sources = list_sources(connection)
        article_total = count_articles(connection)
        qualified_total = count_topics(connection, status="qualified")
        outputs = [
            output
            for output in list_content_outputs(connection)
            if output["approval_status"] != "rejected"
        ]

    active_sources = sum(1 for source in sources if bool(source.get("active_status")))
    social_total = sum(1 for output in outputs if output["content_type"] == "social")
    blog_total = sum(1 for output in outputs if output["content_type"] == "blog")
    content_days = len({to_local_datetime(output["created_at"]).date() for output in outputs})

    primary_metrics = st.columns(3)
    primary_metrics[0].metric("Approved sites", active_sources)
    primary_metrics[1].metric("Articles scanned", article_total)
    primary_metrics[2].metric("Qualified topics", qualified_total)
    content_metrics = st.columns(3)
    content_metrics[0].metric("Social drafts", social_total)
    content_metrics[1].metric("Blog drafts", blog_total)
    content_metrics[2].metric("Content days", content_days)

    action_columns = st.columns([1, 1, 3])
    with action_columns[0]:
        if st.button("Run automation now", type="primary", width="stretch"):
            run_full_automation_from_ui()
    with action_columns[1]:
        if st.button("Generate drafts", width="stretch"):
            generate_drafts_from_ui()

    settings = get_automation_settings()
    saved_times = settings.schedule_times or (settings.schedule_time,)
    upcoming_runs = build_upcoming_run_preview(saved_times, settings.timezone, limit=3)
    st.markdown(
        '<div class="ad-section-heading"><h3>Today\'s automation</h3><p>Saved times and recent run health</p></div>',
        unsafe_allow_html=True,
    )
    schedule_badges = "".join(
        f'<span class="ad-badge is-accent">{escape(run_time)}</span>' for run_time in saved_times
    )
    st.markdown(f'<div class="ad-run-strip">{schedule_badges}</div>', unsafe_allow_html=True)

    run_history = enrich_run_history(list_automation_run_history(limit=6))
    if run_history:
        render_run_history_rows(run_history)
    else:
        next_run = upcoming_runs[0]["next_trigger"] if upcoming_runs else "not scheduled"
        st.markdown(
            f'<div class="ad-empty-panel">No scheduled run has completed yet. Next run: {escape(next_run)}.</div>',
            unsafe_allow_html=True,
        )

    usage = estimate_credit_usage()
    st.markdown(
        '<div class="ad-section-heading"><h3>Research usage</h3><p>Tracked provider consumption</p></div>',
        unsafe_allow_html=True,
    )
    usage_columns = st.columns(3)
    usage_columns[0].metric("Research calls", usage["research_calls"])
    usage_columns[1].metric("Tracked tokens", usage["total_tokens"])
    usage_columns[2].metric("Estimated credits", usage["estimated_api_credits"])

    st.markdown(
        '<div class="ad-section-heading"><h3>Content production</h3><p>Latest draft batches with exact timestamps</p></div>',
        unsafe_allow_html=True,
    )
    batches = group_outputs_by_timestamp(outputs)
    if not batches:
        st.markdown(
            '<div class="ad-empty-panel">No drafts have been created yet.</div>',
            unsafe_allow_html=True,
        )
        return
    render_content_batch_rows(batches[:10])


def render_content_studio() -> None:
    with closing(get_connection()) as connection:
        outputs = [
            output
            for output in list_content_outputs(connection)
            if output["approval_status"] != "rejected"
        ]
        qualified_total = count_topics(connection, status="qualified")

    if not outputs:
        st.markdown(
            """
            <div class="ad-empty-panel">
                <strong>Your qualified topics are safe.</strong><br>
                The last draft build did not finish, so no publishable batch was created.
            </div>
            """,
            unsafe_allow_html=True,
        )
        if qualified_total and st.button(
            f"Create drafts from {qualified_total} qualified topics",
            type="primary",
        ):
            generate_drafts_from_ui()
        return

    st.markdown('<div class="ad-filter-shell">', unsafe_allow_html=True)
    filter_columns = st.columns(4)
    with filter_columns[0]:
        content_filter = st.selectbox("Format", ["All content", "Social media", "Blog"])
    with filter_columns[1]:
        approval_filter = st.selectbox("Review status", ["All statuses", "Pending", "Approved"])
    with filter_columns[2]:
        period_view = st.selectbox("Time view", ["Day", "Week", "Month", "Year"])
    period_options = build_period_options(outputs, period_view)
    with filter_columns[3]:
        selected_period = st.selectbox("Period", list(period_options.keys()))
    st.markdown("</div>", unsafe_allow_html=True)

    period_key = period_options[selected_period]
    period_outputs = [
        output
        for output in outputs
        if output_matches_content_filters(
            output,
            content_filter=content_filter,
            approval_filter=approval_filter,
            period_view=period_view,
            period_key=period_key,
        )
    ]

    period_batches = group_outputs_by_timestamp(period_outputs)
    batch_options: dict[str, tuple[str, list[dict[str, object]]] | None] = {
        f"All timestamps ({len(period_batches)})": None
    }
    for timestamp, batch_outputs in period_batches:
        local_time = to_local_datetime(timestamp)
        label = (
            f"{local_time.strftime('%d %b %Y, %I:%M:%S %p')} "
            f"- {len(batch_outputs)} drafts"
        )
        batch_options[label] = (timestamp, batch_outputs)

    selected_batch_label = st.selectbox(
        "Run timestamp",
        list(batch_options.keys()),
        help="Choose All timestamps to see every content batch in the selected period.",
    )
    selected_batch_option = batch_options[selected_batch_label]
    if selected_batch_option is None:
        filtered_outputs = period_outputs
        visible_batches = period_batches
    else:
        selected_timestamp, filtered_outputs = selected_batch_option
        visible_batches = [(selected_timestamp, filtered_outputs)]

    topic_count = len({int(output["locked_topic_id"]) for output in filtered_outputs})
    timestamp_count = len(visible_batches)
    summary_columns = st.columns([1, 1, 1, 2])
    summary_columns[0].metric("Drafts", len(filtered_outputs))
    summary_columns[1].metric("Topics", topic_count)
    summary_columns[2].metric("Batches", timestamp_count)
    with summary_columns[3]:
        action_columns = st.columns(2)
        if action_columns[0].button("Generate drafts", type="primary", width="stretch"):
            generate_drafts_from_ui()
        if action_columns[1].button("Export approved", width="stretch"):
            export_approved_from_ui()

    if not filtered_outputs:
        st.markdown(
            '<div class="ad-empty-panel">No drafts match these filters.</div>',
            unsafe_allow_html=True,
        )
        return

    st.markdown(
        '<div class="ad-section-heading"><h3>Available timestamps</h3>'
        '<p>Each row is one automation content batch</p></div>',
        unsafe_allow_html=True,
    )
    render_content_batch_rows(visible_batches)

    draft_options = {
        format_content_draft_option(output, include_timestamp=selected_batch_option is None): output
        for output in filtered_outputs
    }
    selected_draft_label = st.selectbox("Draft", list(draft_options.keys()))
    selected_output = draft_options[selected_draft_label]

    local_time = to_local_datetime(selected_output["created_at"])
    st.markdown(
        f'<div class="ad-section-heading"><h3>{escape(str(selected_output["locked_topic"]))}</h3>'
        f'<p>{escape(local_time.strftime("%d %B %Y, %I:%M %p"))}</p></div>',
        unsafe_allow_html=True,
    )
    render_content_library_item(selected_output)


def render_control_room() -> None:
    schedule_tab, sources_tab, research_tab, framework_tab, email_tab = st.tabs(
        ["Schedule", "Sources", "Research", "Content Framework", "Email Alerts"]
    )
    with schedule_tab:
        render_automation_alerts(show_recipients=False, show_smtp_status=False)
    with sources_tab:
        render_source_input()
    with research_tab:
        render_provider_settings()
    with framework_tab:
        render_content_framework_settings()
    with email_tab:
        render_email_alert_settings()


def render_arsenal() -> None:
    st.caption("Technical workspace. Use it only when you need to investigate scraping, rejected topics, or evidence.")
    section_name = st.selectbox("Inspect", ADVANCED_PIPELINE_SECTIONS)
    renderers = {
        "Article Fetch Status": render_article_fetch_status,
        "Extracted Articles": render_extracted_articles,
        "Extracted Topics": render_extracted_topics,
        "Rejected Topics": render_rejected_topics,
        "Qualified Topics": render_qualified_topics,
        "Topic Score Breakdown": render_topic_score_breakdown,
        "Locked Topics": render_locked_topics,
        "Research Dossier": render_research_dossier,
        "Campaign Dissection": render_campaign_dissection,
    }
    renderers[section_name]()


def generate_drafts_from_ui() -> None:
    try:
        apply_runtime_provider_settings()
        with closing(get_connection()) as connection:
            latest_locked_run_id = get_latest_locked_run_id(connection)
        if latest_locked_run_id:
            summary = generate_content_outputs(run_id=latest_locked_run_id)
            blog_count = summary.blog_drafts_created
            social_count = summary.social_drafts_created
        else:
            summary = generate_content_from_qualified_topics()
            blog_count = summary.blog_drafts
            social_count = summary.social_drafts
        st.success(
            f"Generated {blog_count} blog draft(s) and "
            f"{social_count} social draft(s)."
        )
        st.rerun()
    except Exception as exc:
        st.error(str(exc))


def export_approved_from_ui() -> None:
    try:
        summary = export_approved_outputs()
        st.success(f"Exported {summary.approved_outputs} approved draft(s).")
    except Exception as exc:
        st.error(str(exc))


def to_local_datetime(value: object, timezone_name: str = "Asia/Kolkata") -> datetime:
    text_value = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(text_value.replace("Z", "+00:00"))
    except ValueError:
        parsed = datetime.now(UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(load_display_timezone(timezone_name))


def group_outputs_by_timestamp(outputs: list[dict[str, object]]) -> list[tuple[str, list[dict[str, object]]]]:
    grouped: dict[str, list[dict[str, object]]] = {}
    for output in outputs:
        run_id = str(output.get("run_id") or "").strip()
        batch_key = run_id or str(output.get("created_at") or "")
        grouped.setdefault(batch_key, []).append(output)
    batches = [
        (
            min(
                (str(output.get("created_at") or "") for output in batch_outputs),
                key=to_local_datetime,
            ),
            batch_outputs,
        )
        for batch_outputs in grouped.values()
    ]
    return sorted(batches, key=lambda item: to_local_datetime(item[0]), reverse=True)


def format_content_draft_option(
    output: dict[str, object],
    *,
    include_timestamp: bool,
) -> str:
    parts = []
    if include_timestamp:
        parts.append(to_local_datetime(output.get("created_at")).strftime("%d %b, %I:%M:%S %p"))
    topic = re.sub(r"\s+", " ", str(output["locked_topic"])).strip()
    if len(topic) > 96:
        topic = topic[:93].rstrip() + "..."
    parts.extend(
        [
            str(output["content_type"]).title(),
            topic,
            str(output["approval_status"]).title(),
        ]
    )
    return " - ".join(parts)


def render_content_batch_rows(batches: list[tuple[str, list[dict[str, object]]]]) -> None:
    rows = []
    for timestamp, batch_outputs in batches:
        local_time = to_local_datetime(timestamp)
        social_count = sum(1 for output in batch_outputs if output["content_type"] == "social")
        blog_count = sum(1 for output in batch_outputs if output["content_type"] == "blog")
        topic_count = len({int(output["locked_topic_id"]) for output in batch_outputs})
        rows.append(
            f"""
            <div class="ad-timeline-row">
                <div class="ad-time">{escape(local_time.strftime('%d %b %Y - %I:%M %p'))}</div>
                <div>
                    <div class="ad-row-title">{topic_count} topic(s) turned into content</div>
                    <div class="ad-row-meta">{social_count} social - {blog_count} blog</div>
                </div>
                <span class="ad-badge">{len(batch_outputs)} drafts</span>
            </div>
            """
        )
    st.markdown('<div class="ad-timeline">' + "".join(rows) + "</div>", unsafe_allow_html=True)


def render_run_history_rows(run_history: list[dict[str, object]]) -> None:
    rows = []
    for run in run_history:
        schedule_label = f"{run.get('scheduled_date') or ''} {run.get('scheduled_time') or ''}".strip()
        try:
            scheduled_time = datetime.fromisoformat(schedule_label)
            time_label = scheduled_time.strftime("%d %b %Y - %I:%M %p")
        except ValueError:
            time_label = schedule_label or "Schedule time unavailable"
        status = str(run.get("status") or "unknown")
        is_positive_status = status in {"complete", "recovered"}
        default_message = (
            "Queued and waiting for the previous timestamp to finish."
            if status == "queued"
            else "Run not started"
        )
        latest_message = str(
            run.get("latest_message")
            or run.get("error_message")
            or run.get("run_id")
            or default_message
        )
        rows.append(
            f"""
            <div class="ad-timeline-row">
                <div class="ad-time">{escape(time_label)}</div>
                <div>
                    <div class="ad-row-title">Scheduled scan at {escape(str(run.get('scheduled_time') or ''))}</div>
                    <div class="ad-row-meta">{escape(latest_message)}</div>
                </div>
                <span class="ad-badge{' is-accent' if is_positive_status else ''}">{escape(status.title())}</span>
            </div>
            """
        )
    st.markdown('<div class="ad-timeline">' + "".join(rows) + "</div>", unsafe_allow_html=True)


def enrich_run_history(run_history: list[dict[str, object]]) -> list[dict[str, object]]:
    enriched = [dict(run) for run in run_history]
    with closing(get_connection()) as connection:
        for run in enriched:
            if run.get("latest_message") or not run.get("run_id"):
                continue
            latest_event = connection.execute(
                """
                SELECT message, created_at
                FROM run_logs
                WHERE run_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (run["run_id"],),
            ).fetchone()
            if latest_event:
                run["latest_message"] = latest_event["message"]
                run["latest_event_at"] = latest_event["created_at"]
    return enriched


def build_period_options(outputs: list[dict[str, object]], period_view: str) -> dict[str, str]:
    options: dict[str, str] = {}
    for output in outputs:
        local_time = to_local_datetime(output["created_at"])
        key, label = get_period_key_and_label(local_time, period_view)
        options[label] = key
    return dict(
        sorted(
            options.items(),
            key=lambda item: item[1],
            reverse=True,
        )
    )


def get_period_key_and_label(local_time: datetime, period_view: str) -> tuple[str, str]:
    if period_view == "Day":
        return local_time.strftime("%Y-%m-%d"), local_time.strftime("%d %B %Y")
    if period_view == "Week":
        monday = local_time.date() - timedelta(days=local_time.weekday())
        sunday = monday + timedelta(days=6)
        return monday.isoformat(), f"{monday.strftime('%d %b')} - {sunday.strftime('%d %b %Y')}"
    if period_view == "Month":
        return local_time.strftime("%Y-%m"), local_time.strftime("%B %Y")
    return local_time.strftime("%Y"), local_time.strftime("%Y")


def output_matches_content_filters(
    output: dict[str, object],
    *,
    content_filter: str,
    approval_filter: str,
    period_view: str,
    period_key: str,
) -> bool:
    if content_filter == "Social media" and output["content_type"] != "social":
        return False
    if content_filter == "Blog" and output["content_type"] != "blog":
        return False
    if approval_filter != "All statuses" and str(output["approval_status"]).lower() != approval_filter.lower():
        return False
    local_time = to_local_datetime(output["created_at"])
    output_period_key, _ = get_period_key_and_label(local_time, period_view)
    return output_period_key == period_key


def render_content_library_item(output: dict[str, object]) -> None:
    if output["content_type"] == "social":
        render_social_draft(str(output["draft_markdown"] or ""))
    else:
        st.markdown(str(output["draft_markdown"] or ""))
    render_content_review_actions(output)


def render_content_review_actions(output: dict[str, object]) -> None:
    output_id = int(output["id"])
    status = str(output["approval_status"])
    st.divider()
    reviewer_notes = st.text_input(
        "Review note",
        key=f"content_review_note_{output_id}",
        placeholder="Optional note for this draft",
    )
    action_columns = st.columns([1, 1, 3])
    if status != "approved" and action_columns[0].button(
        "Approve",
        type="primary",
        key=f"approve_library_{output_id}",
        width="stretch",
    ):
        set_content_approval(output_id, "approved", reviewer_notes=reviewer_notes)
        st.rerun()
    if action_columns[1].button(
        "Reject",
        key=f"reject_library_{output_id}",
        width="stretch",
    ):
        set_content_approval(output_id, "rejected", reviewer_notes=reviewer_notes)
        st.rerun()


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
    saved_settings = load_research_settings()
    engine = get_research_engine_by_key(
        st.session_state.get("ad_doctor_research_provider")
        or saved_settings.get("provider_key")
    )
    research_model = st.session_state.get("ad_doctor_research_model") or saved_settings.get("model") or engine["model"]
    has_research_key = bool(st.session_state.get("ad_doctor_research_api_key") or saved_settings.get("api_key"))
    has_openrouter_key = bool(st.session_state.get("ad_doctor_openrouter_api_key"))
    st.caption(f"Research engine: {engine['label']} | Model: {research_model}")
    if has_research_key or has_openrouter_key:
        st.success("Session API key ready.")
    else:
        st.info("Using environment keys or local fallback.")


def render_provider_settings() -> None:
    st.write("Choose the research service used to expand qualified topics with cited context.")
    saved_settings = load_research_settings()
    current_engine = get_research_engine_by_key(
        st.session_state.get("ad_doctor_research_provider")
        or saved_settings.get("provider_key")
    )
    labels = get_research_engine_labels()
    current_label = current_engine["label"]
    research_provider = st.selectbox(
        "Research engine",
        labels,
        index=labels.index(current_label) if current_label in labels else 0,
        help="Switch providers when you want a different research source or the current account reaches its limit.",
    )
    selected_engine = get_research_engine_by_label(research_provider)
    if selected_engine["key"] == "perplexity":
        st.success("Perplexity deep research is connected to the live research step.")
    elif selected_engine["key"] == "approved_source_only":
        st.info("Research will use approved-source articles only and consume no AI credits.")
    else:
        st.warning("This provider can be configured, but its live adapter is not connected yet.")
    saved_provider_key = str(saved_settings.get("provider_key") or "")
    saved_key_for_selected_engine = (
        str(saved_settings.get("api_key") or "")
        if selected_engine["key"] == saved_provider_key
        else ""
    )
    with st.form("research_engine_form"):
        research_api_key = st.text_input(
            selected_engine["api_key_label"],
            value=st.session_state.get("ad_doctor_research_api_key", saved_key_for_selected_engine),
            type="password",
            disabled=selected_engine["key"] == "approved_source_only",
            help="Leave blank to use the server environment key when one is configured.",
        )
        with st.expander("Advanced model settings"):
            research_model = st.text_input(
                "Model",
                value=st.session_state.get(
                    "ad_doctor_research_model",
                    saved_settings.get("model")
                    if selected_engine["key"] == saved_provider_key
                    else selected_engine["model"],
                ),
            )
            research_endpoint = st.text_input(
                "API endpoint",
                value=st.session_state.get(
                    "ad_doctor_research_endpoint",
                    saved_settings.get("endpoint")
                    if selected_engine["key"] == saved_provider_key
                    else selected_engine["endpoint"],
                ),
            )
        submitted = st.form_submit_button("Save research setup", type="primary")

    if submitted:
        saved_research = save_research_settings(
            provider_key=selected_engine["key"],
            api_key=research_api_key.strip(),
            model=research_model.strip() or selected_engine["model"],
            endpoint=research_endpoint.strip() or selected_engine["endpoint"],
        )
        st.session_state["ad_doctor_research_provider"] = saved_research["provider_key"]
        st.session_state["ad_doctor_research_api_key"] = saved_research["api_key"]
        st.session_state["ad_doctor_research_model"] = saved_research["model"]
        st.session_state["ad_doctor_research_endpoint"] = saved_research["endpoint"]
        apply_runtime_provider_settings()
        st.success("Research setup saved.")


def render_content_framework_settings() -> None:
    settings = load_content_framework_settings()
    st.write("Edit one clear instruction prompt. New drafts will follow the saved worldview, structure, tone, and checks.")
    st.caption("The minimum 3-source qualification rule is locked at system level and cannot be weakened here.")

    with st.form("content_framework_form"):
        framework_prompt = st.text_area(
            "Master content prompt",
            value=serialize_content_framework_prompt(settings),
            height=620,
            help=(
                "Keep the section headings. Change the instructions under them to adapt the Ad Doctor style, "
                "carousel narrative, tone, lenses, captions, and fact-checking."
            ),
        )

        form_columns = st.columns([1, 1, 2])
        submitted = form_columns[0].form_submit_button("Save prompt", type="primary")
        reset = form_columns[1].form_submit_button("Reset to Ad Doctor default")

    if submitted:
        parsed_settings = parse_content_framework_prompt(framework_prompt, fallback=settings)
        parsed_settings["qualification_rule"] = settings.get(
            "qualification_rule",
            DEFAULT_CONTENT_FRAMEWORK["qualification_rule"],
        )
        saved = save_content_framework_settings(**parsed_settings)
        st.success(f"Content prompt saved: {saved['framework_name']}")
    if reset:
        reset_content_framework_settings()
        st.success("Content prompt reset to the Ad Doctor default.")
        st.rerun()

    preview_settings = load_content_framework_settings()
    render_content_framework_preview(preview_settings)


def render_content_framework_preview(settings: dict[str, str]) -> None:
    with st.expander("Framework preview", expanded=False):
        pillars = parse_json_list(settings.get("content_pillars", ""))
        psychology = parse_json_list(settings.get("psychology_lenses", ""))
        consumers = parse_json_list(settings.get("consumer_lenses", ""))
        culture = parse_json_list(settings.get("culture_lenses", ""))
        slides = parse_carousel_structure(settings.get("carousel_structure", ""))
        preview_columns = st.columns(4)
        preview_columns[0].metric("Pillars", len(pillars))
        preview_columns[1].metric("Slide roles", len(slides))
        preview_columns[2].metric("Psychology lenses", len(psychology))
        preview_columns[3].metric("Consumer/Culture lenses", len(consumers) + len(culture))
        if slides:
            st.dataframe(
                [
                    {
                        "slide": slide.get("slide"),
                        "role": slide.get("role"),
                        "purpose": slide.get("purpose"),
                    }
                    for slide in slides
                ],
                width="stretch",
                hide_index=True,
            )
        st.caption("Saved framework changes apply to newly generated drafts.")


def apply_runtime_provider_settings() -> None:
    try:
        saved_settings = load_research_settings()
    except Exception:
        saved_settings = {}
    provider_key = st.session_state.get("ad_doctor_research_provider") or saved_settings.get("provider_key") or "perplexity"
    engine = get_research_engine_by_key(str(provider_key))
    api_key = st.session_state.get("ad_doctor_research_api_key")
    if api_key is None:
        api_key = saved_settings.get("api_key") or os.getenv(engine["env_var"], "").strip()
    model = st.session_state.get("ad_doctor_research_model") or saved_settings.get("model") or engine["model"]
    endpoint = st.session_state.get("ad_doctor_research_endpoint") or saved_settings.get("endpoint") or engine["endpoint"]
    perplexity_research.ACTIVE_RESEARCH_PROVIDER = engine["key"]
    api_key_value = str(api_key or "").strip()
    perplexity_research.PERPLEXITY_API_KEY = (
        api_key_value or os.getenv("PERPLEXITY_API_KEY", "").strip()
        if engine["key"] == "perplexity"
        else ""
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
    st.write("Connect the source list Ad Doctor is allowed to scan every day.")

    upload_column, sheet_column = st.columns(2)

    with upload_column:
        st.markdown("#### Upload a source file")
        uploaded_file = st.file_uploader(
            "XLS, XLSX, or CSV",
            type=["xls", "xlsx", "csv"],
        )
        if uploaded_file and st.button("Import uploaded file", type="primary"):
            try:
                result = import_sources_from_file(uploaded_file, filename=uploaded_file.name)
                render_import_result(result)
            except Exception as exc:
                st.error(str(exc))

    with sheet_column:
        st.markdown("#### Connect Google Sheets")
        sheet_url = st.text_input("Google Sheet link")
        if st.button("Connect sheet"):
            try:
                result = import_sources_from_google_sheet(sheet_url)
                render_import_result(result)
            except Exception as exc:
                st.error(str(exc))

    with st.expander("File format help"):
        expected_columns = ", ".join(SOURCE_COLUMNS)
        st.write(f"Supported columns: {expected_columns}")
        st.caption("The importer accepts common URL column aliases and safely updates existing sources.")

    with closing(get_connection()) as connection:
        sources = list_sources(connection)

    st.markdown("#### Connected sources")
    if not sources:
        st.info("No sources imported yet.")
        return

    active_count = sum(1 for source in sources if source["active_status"] == 1)
    inactive_count = len(sources) - active_count
    metric_columns = st.columns(2)
    metric_columns[0].metric("Active", active_count)
    metric_columns[1].metric("Inactive", inactive_count)
    with st.expander(f"View all {len(sources)} connected sources"):
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


def render_rejected_topics() -> None:
    with closing(get_connection()) as connection:
        rejected_topics = [
            topic
            for topic in list_topics(connection, limit=None)
            if str(topic.get("status") or "").lower() == "rejected"
        ]

    st.write("Topics that currently fail the mandatory 3-source same-event qualification rule.")
    st.metric("Rejected topics", len(rejected_topics))
    if not rejected_topics:
        st.info("No rejected topics are stored.")
        return
    st.dataframe(
        [
            {
                "topic": topic["exact_topic"],
                "unique_sources": topic["source_coverage_count"],
                "articles": topic["article_count"],
                "status": topic["status"],
                "created_at": topic["created_at"],
            }
            for topic in rejected_topics
        ],
        width="stretch",
        hide_index=True,
    )


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
            "Framework Used",
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
            "Framework Used",
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
        st.success(
            "Automation is active. Every selected timestamp is watched independently, "
            "even when nobody has the website open."
        )
    elif scheduler_status.enabled_by_env:
        st.warning(scheduler_status.message)
    else:
        st.warning("Automation is paused for this deployment.")

    schedule_times = settings.schedule_times or (settings.schedule_time,)
    settings_columns = st.columns([1, 2, 1, 1])
    with settings_columns[0]:
        enabled = st.checkbox("Auto-run enabled", value=settings.enabled)
    with settings_columns[1]:
        time_options = build_daily_time_options(extra_times=schedule_times)
        selected_schedule_times = st.multiselect(
            "Daily run times",
            options=time_options,
            default=list(schedule_times),
            help="Select one or more times. The automation will trigger once per selected time.",
            placeholder="Select run times",
        )
        try:
            parsed_schedule_times = parse_schedule_times(selected_schedule_times)
            st.caption(f"Will run daily at: {', '.join(parsed_schedule_times)}")
        except Exception:
            st.caption("Select at least one run time.")
    with settings_columns[2]:
        notify_success = st.checkbox("Mail when done", value=settings.notify_on_success)
    with settings_columns[3]:
        notify_failure = st.checkbox("Mail on failure", value=settings.notify_on_failure)

    budget = settings.fetch_source_time_budget_seconds
    with st.expander("Scan performance"):
        budget = st.number_input(
            "Maximum seconds per source",
            min_value=60,
            max_value=600,
            value=settings.fetch_source_time_budget_seconds,
            step=30,
            help="Increase this only when large websites are being cut off before their scan completes.",
        )

    if st.button("Save schedule", type="primary"):
        try:
            normalized_schedule_times = parse_schedule_times(selected_schedule_times)
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
                if os.getenv("AD_DOCTOR_SCHEDULER_MODE", "app").strip().lower() == "worker":
                    st.success(
                        "Schedule saved. The cloud worker will pick up every selected timestamp within 30 seconds."
                    )
                else:
                    st.info(
                        "Settings saved. The local scheduler will use these run times while this computer is running."
                    )
            settings = saved
            schedule_times = settings.schedule_times or (settings.schedule_time,)
        except Exception as exc:
            st.error(str(exc))

    render_active_schedule_summary(settings)

    history = list_automation_run_history(limit=25)
    if history:
        with st.expander("Recent scheduled runs"):
            st.dataframe(history, width="stretch", hide_index=True)
    else:
        st.info("No scheduled auto-runs have been recorded yet.")

    if not show_recipients and not show_smtp_status:
        return

    st.divider()
    render_email_alert_settings(show_smtp_status=show_smtp_status)


def render_email_alert_settings(*, show_smtp_status: bool = True) -> None:
    st.write("Add everyone who should be notified when a content run finishes or fails.")
    recipient_columns = st.columns([2, 2, 1])
    with recipient_columns[0]:
        recipient_email = st.text_input("User email")
    with recipient_columns[1]:
        recipient_name = st.text_input("Display name")
    with recipient_columns[2]:
        recipient_enabled = st.checkbox("Enabled", value=True)
    if st.button("Save recipient", type="primary"):
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
        with st.expander(f"Notification recipients ({len(users)})", expanded=True):
            st.dataframe(
                [
                    {
                        "email": user["email"],
                        "name": user["display_name"],
                        "enabled": bool(user["notifications_enabled"]),
                        "last_seen": user["last_login_at"],
                    }
                    for user in users
                ],
                width="stretch",
                hide_index=True,
            )
    else:
        st.info("No notification users saved yet.")

    if show_smtp_status:
        smtp_settings = load_smtp_settings()
        if smtp_settings.is_ready:
            st.success("Email sending is configured.")
        else:
            st.warning(f"Email sending needs: {', '.join(smtp_settings.missing_fields)}")

        with st.expander("Sender connection", expanded=not smtp_settings.is_ready):
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
                submitted_smtp = st.form_submit_button("Save sender connection", type="primary")
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
                        st.success("Email sender connection saved.")
                    else:
                        st.warning(f"Saved, but still missing: {', '.join(saved_smtp.missing_fields)}")
                except Exception as exc:
                    st.error(str(exc))

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
            with st.expander("Email delivery history"):
                st.dataframe(events, width="stretch", hide_index=True)
        else:
            st.info("No notification events yet.")


def parse_schedule_time(value: str) -> tuple[int, int]:
    try:
        hour_text, minute_text = value.split(":", 1)
        return int(hour_text), int(minute_text)
    except Exception:
        return 9, 0


def build_daily_time_options(
    *,
    step_minutes: int = 15,
    extra_times: tuple[str, ...] | list[str] = (),
) -> list[str]:
    options = {
        f"{minutes // 60:02d}:{minutes % 60:02d}"
        for minutes in range(0, 24 * 60, step_minutes)
    }
    for time_value in extra_times:
        try:
            parsed = parse_schedule_times([time_value])
        except Exception:
            continue
        options.update(parsed)
    return sorted(options, key=parse_schedule_time)


def render_active_schedule_summary(settings) -> None:
    saved_times = settings.schedule_times or (settings.schedule_time,)
    st.markdown("#### Saved daily schedule")
    badges = "".join(
        f'<span class="ad-badge is-accent">{escape(run_time)}</span>' for run_time in saved_times
    )
    st.markdown(f'<div class="ad-run-strip">{badges}</div>', unsafe_allow_html=True)
    upcoming_runs = build_upcoming_run_preview(saved_times, settings.timezone, limit=5)
    if upcoming_runs:
        st.dataframe(upcoming_runs, width="stretch", hide_index=True)
    else:
        st.info("No upcoming run preview is available.")


def build_upcoming_run_preview(
    schedule_times: tuple[str, ...] | list[str],
    timezone_name: str,
    *,
    limit: int = 5,
) -> list[dict[str, str]]:
    timezone = load_display_timezone(timezone_name)
    now = datetime.now(timezone)
    upcoming = []
    for days_ahead in range(0, 8):
        day = now.date() + timedelta(days=days_ahead)
        for schedule_time in schedule_times:
            hour, minute = parse_schedule_time(schedule_time)
            candidate = datetime(
                day.year,
                day.month,
                day.day,
                hour,
                minute,
                tzinfo=timezone,
            )
            if candidate <= now:
                continue
            upcoming.append(
                {
                    "next_trigger": candidate.strftime("%Y-%m-%d %H:%M"),
                    "run_time": schedule_time,
                    "timezone": timezone_name,
                }
            )
    return sorted(upcoming, key=lambda row: row["next_trigger"])[:limit]


def load_display_timezone(timezone_name: str):
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("Asia/Kolkata")


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
