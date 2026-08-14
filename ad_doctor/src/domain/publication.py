from dataclasses import asdict,dataclass,field
from datetime import datetime
from enum import StrEnum
from typing import Any
from src.domain.errors import DomainValidationError
class PublishingStatus(StrEnum):NOT_READY='not_ready';PUBLISHING='publishing';PUBLISHED='published';FAILED='failed';SUPERSEDED='superseded';ARCHIVED='archived'
class Visibility(StrEnum):PRIVATE='private';SPECIFIC_READERS='specific_readers';LINK_VIEWER='link_viewer'
@dataclass(frozen=True,slots=True)
class Publication:
 publication_id:str;case_id:str;content_package_id:str;package_version:int;approval_event_id:str;quality_review_id:str;publication_version:int;publishing_status:PublishingStatus;document_provider:str;google_document_id:str|None;google_document_url:str|None;google_drive_folder_id:str|None;google_drive_file_id:str|None;document_title:str;document_checksum:str;visibility:Visibility;published_at:datetime|None;published_by:str;originating_run_id:str|None;created_at:datetime;updated_at:datetime;supersedes_publication_id:str|None;metadata:dict[str,Any];idempotency_key:str
 def __post_init__(self):
  if not self.publication_id or not self.case_id or self.package_version<1 or self.publication_version<1:raise DomainValidationError('Publication identity and versions are required')
  if self.publishing_status==PublishingStatus.PUBLISHED and (not self.google_document_id or not self.google_document_url):raise DomainValidationError('Published records require document ID and URL')
 def to_dict(self):
  d=asdict(self);d['publishing_status']=self.publishing_status.value;d['visibility']=self.visibility.value
  for k in ('published_at','created_at','updated_at'):d[k]=d[k].isoformat() if d[k] else None
  return d
 @classmethod
 def from_dict(cls,p):
  q=dict(p);q['publishing_status']=PublishingStatus(q['publishing_status']);q['visibility']=Visibility(q['visibility'])
  for k in ('published_at','created_at','updated_at'):q[k]=datetime.fromisoformat(q[k]) if q.get(k) else None
  return cls(**q)
@dataclass(frozen=True,slots=True)
class FinalDocument:
 title:str;subtitle:str;case_metadata:dict[str,str];executive_diagnosis:str;source_coverage_summary:str;symptoms:str;patient_history:str;diagnosis:str;treatment:str;results:str;doctors_opinion:str;prescription:str;carousel:tuple[dict[str,Any],...];social_caption:str;article:str;design_direction:str;quality_summary:str;uncertainties:tuple[str,...];disclosures:tuple[str,...];sources:tuple[dict[str,str],...];publication_metadata:dict[str,str]
 def checksum(self):
  import hashlib,json
  return hashlib.sha256(json.dumps(asdict(self),sort_keys=True,ensure_ascii=False).encode()).hexdigest()
class SharingPolicy:
 def __init__(self,visibility:Visibility=Visibility.PRIVATE,readers:tuple[str,...]=(),link_viewer_enabled:bool=False):
  if visibility==Visibility.LINK_VIEWER and not link_viewer_enabled:raise DomainValidationError('Link-viewer sharing requires explicit configuration')
  if visibility==Visibility.SPECIFIC_READERS and not readers:raise DomainValidationError('Specific readers must be explicit')
  self.visibility=visibility;self.readers=readers
 def delivery_blockers(self):return () if self.visibility==Visibility.LINK_VIEWER else ('document_not_accessible_to_subscribers',)
