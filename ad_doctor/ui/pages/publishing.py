from ui.components import page_header,empty_state
def render(st,service,actions):
 page_header(st,'Publishing','Review publishing eligibility, documents and distribution readiness.');vm=service.get('publishing')
 if not vm:empty_state(st,'No publishing records','Approved and quality-passed cases will appear here.','Complete editorial approval first.');return
 c1,c2=st.columns(2);c1.metric('Ready',len(vm.ready));c2.metric('Blocked',len(vm.blocked));[st.warning(x) for x in vm.warnings]
 st.subheader('Publishing ready');[st.write(f'✓ **{x.title}** — {x.meta}') for x in vm.ready];st.subheader('Blocked');[st.write(f'○ **{x.title}** — {x.details.get("publication","Review required")}') for x in vm.blocked];st.button('Publish selected case',disabled=True,help='Live publishing is disabled in Phase 4A.')
