from ui.components import empty_state, page_header, status_badge
from ui.components.filter_bar import filter_bar
from ui.components.pagination import pagination_controls


def render(st, service, actions):
    page_header(st, "Articles", "Search extracted coverage without exposing raw page HTML.")
    search, status, sort = filter_bar(st, "articles", options=("extracted", "Linked", "Unlinked", "Warning"))
    page_no = int(st.session_state.get("articles_page", 1))
    items, meta = service.list("articles", search=search, status=status, sort=sort, page_number=page_no, page_size=10)
    st.caption(f"{meta['total']} article(s) match this view")
    if not items:
        empty_state(st, "No articles found", "Nothing matches the current filters.", "Reset filters or run article discovery.")
        return
    labels = [f"{item.title} · {item.subtitle}" for item in items]
    selected = st.selectbox("Article", labels)
    item = items[labels.index(selected)]
    with st.container(border=True):
        st.subheader(item.title)
        status_badge(st, item.status)
        details = item.details
        st.caption(f"{details['publication']} · Published: {details['date'] or 'Unknown'} · Fetched: {details['fetch_date'] or 'Unknown'}")
        st.write(f"**Topic:** {details['topic']}")
        st.write(f"**Extraction:** {details['extraction_status']}")
        st.write(details["summary"])
        st.markdown("#### Extracted text")
        st.write(details["article_text"])
        if details.get("article_url"):
            st.link_button("Open original article", details["article_url"])
    pagination_controls(st, "articles", meta)
