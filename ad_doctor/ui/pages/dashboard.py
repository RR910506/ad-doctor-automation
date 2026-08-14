from ui.components import page_header,empty_state,status_badge
def render(st,service,actions):
 page_header(st,'Dashboard','A clear view of today’s marketing-intelligence workflow.');vm=service.get('dashboard')
 if not vm:empty_state(st,'Your workspace is ready','No live overview is connected to the modern shell yet.','Open the Legacy Workspace from Settings or enable demo mode.');return
 metrics=list(vm.metrics.items())
 for start in range(0,len(metrics),3):
  cols=st.columns(3)
  for col,(label,value) in zip(cols,metrics[start:start+3]):col.metric(label,value)
 st.subheader('Needs attention')
 for item in vm.attention:
  with st.container(border=True):st.write(f'**{item.title}**');st.caption(item.subtitle);status_badge(st,item.status)
 st.subheader('Recent cases')
 for item in vm.recent_cases:
  with st.container(border=True):st.write(f'**{item.title}**');st.caption(item.subtitle);status_badge(st,item.status)
 st.subheader('System health');cols=st.columns(3)
 for i,(name,state) in enumerate(vm.health.items()):cols[i%3].metric(name,state)
 st.caption(f'Last successful run: {vm.last_run} · Next scheduled run: {vm.next_run}')
 c1,c2=st.columns([1,1]);c1.button('Run automation',type='primary',disabled=True,help='Read-only in Phase 4A');c2.button('Review qualified topics',disabled=True,help='Open Topic Qualification from navigation.')
