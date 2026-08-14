from ui.components import page_header,empty_state,status_badge
from ui.components.filter_bar import filter_bar
from ui.components.pagination import pagination_controls
def render(st,service,actions):
 page_header(st,'Topic Qualification','Qualify a development only when it is covered by three distinct approved publications.');search,status,sort=filter_bar(st,'qualification',options=('Qualified','Awaiting third publication','Rejected'));page_no=int(st.session_state.get('qualification_page',1));items,meta=service.list('qualification',search=search,status=status,sort=sort,page_number=page_no,page_size=10)
 if not items:empty_state(st,'No candidate topics','No topics currently need qualification.','Run article discovery or reset the filters.');return
 left,right=st.columns([1,1.5]);labels=[f'{x.title} · {x.details.get("count",0)} publications' for x in items];selected=left.radio('Candidate topics',labels,label_visibility='collapsed');item=items[labels.index(selected)];right.subheader(item.title);status_badge(right,item.status);right.metric('Distinct publications',item.details.get('count',0));right.info(item.details.get('reason',''))
 right.markdown('#### Exact supporting articles')
 for article in item.details.get('supporting',[]):right.write(f'✓ {article.title} — {article.subtitle}')
 excluded=item.details.get('excluded',[])
 if excluded:right.markdown('#### Excluded unrelated coverage');[right.caption(f'Excluded: {x}') for x in excluded]
 right.button('Re-evaluate topic',disabled=True,help='Read-only in Phase 4A')
 pagination_controls(st,'qualification',meta)
