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
