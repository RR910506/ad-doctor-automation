"""Thin Streamlit entry point for the Ad Doctor workspace."""
from ui.app_shell import render_app as render_dashboard_shell
from ui.legacy_compat import (
    build_period_options,
    format_content_draft_option,
    group_outputs_by_timestamp,
    output_matches_content_filters,
)

if __name__ == "__main__":
    render_dashboard_shell()
