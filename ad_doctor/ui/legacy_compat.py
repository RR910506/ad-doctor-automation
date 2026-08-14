"""Lazy compatibility exports for tests and integrations using the former app module."""


def group_outputs_by_timestamp(outputs):
    from ui.legacy_app import group_outputs_by_timestamp as legacy_function
    return legacy_function(outputs)


def format_content_draft_option(output, *, include_timestamp):
    from ui.legacy_app import format_content_draft_option as legacy_function
    return legacy_function(output, include_timestamp=include_timestamp)


def build_period_options(outputs, period_view):
    from ui.legacy_app import build_period_options as legacy_function
    return legacy_function(outputs, period_view)


def output_matches_content_filters(output, *, content_filter, approval_filter, period_view, period_key):
    from ui.legacy_app import output_matches_content_filters as legacy_function
    return legacy_function(output, content_filter=content_filter, approval_filter=approval_filter, period_view=period_view, period_key=period_key)
