from __future__ import annotations
import os
import re
from dataclasses import replace
from ui.fixture_data import demo_payload
from ui.components.pagination import paginate
from ui.status_vocabulary import status_display
from src.security.redaction import redact
def demo_mode_enabled(environ=None):return str((environ or os.environ).get('AD_DOCTOR_UI_DEMO_MODE','false')).lower() in {'1','true','yes','on'}
class UIQueryService:
 def __init__(self,demo=None,state='populated'):
  self.demo=demo_mode_enabled() if demo is None else demo
  self.state=state if self.demo and state in {'populated','empty','loading','warning'} else 'populated'
  populated=demo_payload() if self.demo else None
  if populated is not None and self.state=='warning':populated['automation']=replace(populated['automation'],enabled=False,next_run='Paused')
  self._payload=populated if populated is not None and self.state in {'populated','warning'} else {'dashboard':None,'sources':(),'articles':(),'qualification':(),'cases':(),'content_studio':None,'publishing':None,'subscribers':(),'automation':None,'logs':(),'settings':()}
 @property
 def demo_label(self):return 'Demo data — no production services' if self.demo else None
 def get(self,page):return self._payload.get(page,())
 def list(self,page,*,search='',status='All',sort='Newest first',page_number=1,page_size=10,filters=None):
  items=list(self.get(page) or ());term=search.strip().lower();filters=filters or {}
  if page=='logs':items=[replace(x,title=str(redact(x.title)),subtitle=re.sub(r'([A-Za-z0-9._%+-])[^@\s]*(@[^\s]+)',r'\1***\2',str(redact(x.subtitle)))) for x in items]
  if term:items=[x for x in items if term in (x.title+' '+x.subtitle).lower()]
  if status!='All':items=[x for x in items if status.lower() in {x.status.lower(),x.meta.lower(),status_display(x.status).label.lower()}]
  for key,value in filters.items():
   if value not in (None,'All',''):items=[x for x in items if str(x.details.get(key,'')).lower()==str(value).lower()]
  if sort=='A–Z':items.sort(key=lambda x:x.title.lower())
  elif sort=='Oldest first':items.reverse()
  return paginate(items,page_number,page_size)
