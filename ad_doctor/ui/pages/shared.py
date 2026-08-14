from ui.components import empty_state,card_list
from ui.components.filter_bar import filter_bar
from ui.components.pagination import pagination_controls
from ui.status_vocabulary import status_display
def render_directory(st,service,page,title,description,statuses=(),display='meta'):
 from ui.components.page_header import page_header
 page_header(st,title,description);search,status,sort=filter_bar(st,page,options=statuses);page_no=int(st.session_state.get(f'{page}_page',1));items,meta=service.list(page,search=search,status=status,sort=sort,page_number=page_no)
 if not items:empty_state(st,f'No {title.lower()} found','Nothing matches the current view.','Reset filters or connect a data source to continue.');return
 def display_meta(item):
  friendly=status_display(item.status).label
  if display=='status':return friendly
  if display=='status_meta':return f'{friendly} · {item.meta}'
  return item.meta or friendly
 card_list(st,[{'title':x.title,'subtitle':x.subtitle,'meta':display_meta(x)} for x in items]);pagination_controls(st,page,meta)
