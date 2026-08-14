from ui.components import empty_state, page_header, status_badge
from ui.components.filter_bar import filter_bar
from ui.components.pagination import pagination_controls


def _result(st, result):
    (st.success if result.success else st.error)(result.user_message)
    if result.success and result.refresh_required:
        st.rerun()


def render(st, service, actions):
    page_header(st, "Sources", "Manage the approved publications that power Ad Doctor intelligence.")
    search, status, sort = filter_bar(st, "sources", options=("active", "inactive"))
    page_no = int(st.session_state.get("sources_page", 1))
    items, meta = service.list("sources", search=search, status=status, sort=sort, page_number=page_no, page_size=10)
    st.caption(f"{meta['total']} source(s) match this view")
    if not items:
        empty_state(st, "No sources found", "Nothing matches the current filters.", "Reset the filters or import approved sources.")
    for item in items:
        with st.container(border=True):
            title, state = st.columns([4, 1])
            title.write(f"**{item.title}**")
            title.caption(item.subtitle)
            status_badge(state, item.status)
            details = item.details
            st.caption(f"Category: {details['category']} · Country: {details['country']} · Credibility tier: {details['credibility_tier']} · Stored articles: {details['articles']} · Last scan: {details['last_scan']}")
            confirm = st.checkbox(f"Confirm {'deactivation' if details['active'] else 'activation'}", key=f"confirm_source_{details['id']}")
            if st.button("Deactivate" if details["active"] else "Activate", key=f"toggle_source_{details['id']}", disabled=not confirm):
                _result(st, actions.set_source_active(details["id"], not details["active"], confirmed=confirm))
    pagination_controls(st, "sources", meta)
    with st.expander("Import approved sources"):
        uploaded = st.file_uploader("CSV or Excel source list", type=["csv", "xls", "xlsx"])
        confirm_file = st.checkbox("Confirm file import", key="confirm_source_file_import")
        if st.button("Import file", disabled=uploaded is None or not confirm_file):
            _result(st, actions.import_sources_from_file(uploaded, filename=getattr(uploaded, "name", None), confirmed=confirm_file))
        sheet_url = st.text_input("Google Sheet URL", placeholder="https://docs.google.com/spreadsheets/d/…")
        confirm_sheet = st.checkbox("Confirm Google Sheet import", key="confirm_source_sheet_import")
        if st.button("Import Google Sheet", disabled=not sheet_url.strip() or not confirm_sheet):
            _result(st, actions.import_sources_from_google_sheet(sheet_url, confirmed=confirm_sheet))
