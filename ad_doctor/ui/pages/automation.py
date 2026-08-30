from ui.components import empty_state, page_header


def render(st, service, actions):
    page_header(st, 'Automation', 'Review schedules and recent runs without exposing worker internals.')
    vm = service.get('automation')
    if not vm:
        empty_state(st, 'Automation status unavailable', 'The modern read-only adapter is not connected.', 'Open Legacy Workspace from Settings for existing controls.')
        return
    first = st.columns(2)
    first[0].metric('Status', 'Enabled' if vm.enabled else 'Paused')
    first[1].metric('Next run', vm.next_run)
    second = st.columns(2)
    second[0].metric('Last run', vm.last_run)
    second[1].metric('Timezone', vm.timezone)
    metrics=vm.browser_metrics or {}
    st.subheader('Browser research worker')
    browser=st.columns(4)
    browser[0].metric('Sources monitored',metrics.get('active_sources',metrics.get('sources_monitored',0)))
    browser[1].metric('Sources checked today',metrics.get('sources_checked_today',0))
    browser[2].metric('URLs discovered',metrics.get('urls_discovered',0))
    browser[3].metric('Articles fetched',metrics.get('articles_fetched',0))
    extraction=st.columns(3)
    extraction[0].metric('HTTP extractions',metrics.get('http_extractions',0))
    extraction[1].metric('Browser fallbacks',metrics.get('browser_fallbacks',0))
    extraction[2].metric('Extraction failures',metrics.get('extraction_failures',0))
    outcomes=st.columns(3)
    outcomes[0].metric('New topics',metrics.get('new_topics',0))
    outcomes[1].metric('Qualified cases',metrics.get('qualified_cases',0))
    outcomes[2].metric('Next cycle',vm.next_run)
    st.caption(f"Last browser cycle: {metrics.get('finished_at') or metrics.get('started_at') or 'Not run'} · Status: {metrics.get('status','Not run')}")
    st.caption('Configured times: ' + ', '.join(vm.times))
    st.subheader('Recent runs')
    for item in vm.history:
        st.write(f'**{item.title}** · {item.subtitle} · {item.meta}')
    confirmed = st.checkbox('I understand this starts a live automation run', key='confirm_manual_automation')
    if st.button('Run automation now', type='primary', disabled=not confirmed):
        with st.spinner('Running the existing Ad Doctor pipeline…'):
            result = actions.run_automation_now(confirmed=confirmed)
        (st.success if result.success else st.error)(result.user_message)
        if result.run_id:
            st.caption(f'Run ID: {result.run_id}')
        if result.success and result.refresh_required:
            st.rerun()
