def filter_bar(st, key, search_label='Search', options=()):
    cols = st.columns([2, 1, 1, .7])
    search = cols[0].text_input(search_label, key=f'{key}_search', placeholder='Type to filter…')
    status = cols[1].selectbox('Status', ['All', *options], key=f'{key}_status')
    sort = cols[2].selectbox('Sort', ['Newest first', 'Oldest first', 'A–Z'], key=f'{key}_sort')
    if cols[3].button('Reset', key=f'{key}_reset', use_container_width=True):
        for suffix in ('search', 'status', 'sort', 'page'):
            st.session_state.pop(f'{key}_{suffix}', None)
        st.rerun()
    return search, status, sort
