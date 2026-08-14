from dataclasses import dataclass,field
from typing import Any
@dataclass(frozen=True,slots=True)
class ListItem:title:str;subtitle:str='';status:str='';meta:str='';details:dict[str,Any]=field(default_factory=dict)
@dataclass(frozen=True,slots=True)
class DashboardViewModel:metrics:dict[str,str];attention:tuple[ListItem,...];recent_cases:tuple[ListItem,...];health:dict[str,str];last_run:str;next_run:str
@dataclass(frozen=True,slots=True)
class SourceListItem(ListItem):pass
@dataclass(frozen=True,slots=True)
class ArticleListItem(ListItem):pass
@dataclass(frozen=True,slots=True)
class TopicQualificationItem(ListItem):pass
@dataclass(frozen=True,slots=True)
class CaseListItem(ListItem):pass
@dataclass(frozen=True,slots=True)
class ContentStudioViewModel:queue:tuple[ListItem,...];selected:dict[str,Any];packages:dict[str,dict[str,Any]]=field(default_factory=dict)
@dataclass(frozen=True,slots=True)
class PublishingViewModel:ready:tuple[ListItem,...];blocked:tuple[ListItem,...];history:tuple[ListItem,...];warnings:tuple[str,...]
@dataclass(frozen=True,slots=True)
class SubscriberListItem(ListItem):pass
@dataclass(frozen=True,slots=True)
class AutomationViewModel:enabled:bool;next_run:str;last_run:str;timezone:str;times:tuple[str,...];history:tuple[ListItem,...]
@dataclass(frozen=True,slots=True)
class LogListItem(ListItem):pass
@dataclass(frozen=True,slots=True)
class ProviderHealthViewModel:name:str;status:str;message:str;source:str
