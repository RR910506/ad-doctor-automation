"""A guided home screen for the editorial workspace."""
from ui.components import page_header, empty_state, status_badge


def _navigate(st, page):
    st.query_params['page'] = page
    st.rerun()


def render(st, service, actions):
    page_header(st, 'Your editorial workspace',
                'Find the signal. Build the evidence. Publish your point of view.')
    vm = service.get('dashboard')
    if service.demo:
        st.info('Sample workspace · These examples do not represent live research or published content.')
    st.markdown('### What would you like to do?')
    steps = (
        ('01 · Sources', 'Choose the publications you trust.', 'Manage sources', 'sources'),
        ('02 · Evidence', 'Find topics covered by three publications.', 'Review topics', 'qualification'),
        ('03 · Create', 'Review research, diagnosis and drafts.', 'Open Content Studio', 'content_studio'),
        ('04 · Publish', 'Check approvals and final documents.', 'Review publishing', 'publishing'),
    )
    for col, (title, description, label, page) in zip(st.columns(4), steps):
        with col.container(border=True):
            st.markdown(f'**{title}**')
            st.caption(description)
            if st.button(label, key=f'home_{page}', use_container_width=True):
                _navigate(st, page)
    if not vm:
        empty_state(st, 'Start with your approved publications',
                    'Import your source list to begin building your editorial workspace.',
                    'Choose Manage sources above to upload a CSV or spreadsheet.')
        return
    if str(vm.metrics.get('Active sources', '0')) == '0':
        st.info('Start here: add your approved publications in Sources. Topics need coverage from at least three distinct publications before research can begin.')
    st.markdown('### Your pipeline at a glance')
    preferred = ('Active sources', 'Articles scanned', 'Qualified topics', 'Approval pending')
    summary = [(key, vm.metrics[key]) for key in preferred if key in vm.metrics]
    for col, (label, value) in zip(st.columns(max(1, len(summary))), summary):
        col.metric(label, value)
    left, right = st.columns([1.2, 1])
    with left:
        st.subheader('Needs your attention')
        if not vm.attention:
            st.caption('No items need your attention yet. Start by adding sources or reviewing your topics.')
        for item in vm.attention:
            with st.container(border=True):
                st.write(f'**{item.title}**')
                st.caption(item.subtitle)
                status_badge(st, item.status)
        if st.button('Review drafts', type='primary'):
            _navigate(st, 'content_studio')
    with right:
        st.subheader('Recent cases')
        if not vm.recent_cases:
            st.caption('Your marketing cases will appear here as topics qualify.')
        for item in vm.recent_cases:
            with st.container(border=True):
                st.write(f'**{item.title}**')
                st.caption(item.subtitle)
                status_badge(st, item.status)
        if st.button('View all cases'):
            _navigate(st, 'cases')
    with st.expander('System status and pipeline details'):
        st.caption('Configuration status does not confirm that a live integration has been tested.')
        cols = st.columns(3)
        for i, (name, state) in enumerate(vm.health.items()):
            cols[i % 3].metric(name, state)
        for label, value in vm.metrics.items():
            if label not in preferred:
                st.write(f'{label}: {value}')
        st.caption(f'Last successful run: {vm.last_run} · Next scheduled run: {vm.next_run}')
        if st.button('Manage automation'):
            _navigate(st, 'automation')
    st.caption('Human approval is required before publishing or email delivery.')
