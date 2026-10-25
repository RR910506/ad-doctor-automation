from dataclasses import dataclass
@dataclass(frozen=True,slots=True)
class NavItem:key:str;label:str;description:str;icon:str
GROUPS={
'WORKSPACE':(NavItem('dashboard','Dashboard','Executive overview','◫'),NavItem('sources','Sources','Publication directory','◎'),NavItem('articles','Articles','Extracted article library','▤'),NavItem('qualification','Topic Qualification','Three-publication qualification','✓'),NavItem('cases','Cases','Marketing diagnosis cases','◇'),NavItem('content_studio','Content Studio','Editorial review workspace','✦')),
'DISTRIBUTION':(NavItem('publishing','Publishing','Final documents and distribution','↗'),NavItem('subscribers','Subscribers','Consent and audience management','◉')),
'SYSTEM':(NavItem('automation','Automation','Schedules and runs','⌁'),NavItem('logs','Logs','Operational activity','≡'),NavItem('settings','Settings','Providers and configuration','⚙'))}
ALL={x.key:x for items in GROUPS.values() for x in items}
ALL['model_lab']=NavItem('model_lab','AI tools & comparisons','Research and writing experiments','✦')
MAIN_ITEMS=(NavItem('dashboard','Today','Collected stories and your next step','◫'),NavItem('content_studio','My content','Review prepared content','✎'),NavItem('publishing','Publishing','Approved documents and delivery','↗'))
EXPERT_ITEMS=tuple(ALL[key] for key in ('sources','articles','qualification','cases','model_lab','subscribers','automation','logs','settings'))
def resolve_page(value):return value if value in ALL or value=='legacy' else 'dashboard'
