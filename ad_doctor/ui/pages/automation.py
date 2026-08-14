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
    st.button('Run automation now', disabled=True, help='Confirmation and live action remain in Legacy Workspace during Phase 4A.')
