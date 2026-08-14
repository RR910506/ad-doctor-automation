from dataclasses import dataclass
@dataclass(frozen=True,slots=True)
class NavItem:key:str;label:str;description:str;icon:str
GROUPS={
'WORKSPACE':(NavItem('dashboard','Dashboard','Executive overview','◫'),NavItem('sources','Sources','Publication directory','◎'),NavItem('articles','Articles','Extracted article library','▤'),NavItem('qualification','Topic Qualification','Three-publication qualification','✓'),NavItem('cases','Cases','Marketing diagnosis cases','◇'),NavItem('content_studio','Content Studio','Editorial review workspace','✦')),
'DISTRIBUTION':(NavItem('publishing','Publishing','Final documents and distribution','↗'),NavItem('subscribers','Subscribers','Consent and audience management','◉')),
'SYSTEM':(NavItem('automation','Automation','Schedules and runs','⌁'),NavItem('logs','Logs','Operational activity','≡'),NavItem('settings','Settings','Providers and configuration','⚙'))}
ALL={x.key:x for items in GROUPS.values() for x in items}
def resolve_page(value):return value if value in ALL or value=='legacy' else 'dashboard'
