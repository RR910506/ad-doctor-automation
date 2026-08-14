from __future__ import annotations
import hashlib,json
from contextlib import closing
from dataclasses import replace
from datetime import UTC,datetime
from pathlib import Path
from uuid import uuid4
from src.db import get_connection
from src.domain.errors import DomainValidationError,VersionConflictError
from src.domain.publication import *
from src.domain.workflow import ActorType,WorkflowStatus
from src.repositories.sqlite_publishing_repositories import SQLitePublicationRepository
from src.services.case_lifecycle_service import CaseLifecycleService
from src.services.final_document_service import DocumentBuilder,FinalDocumentMapper,sanitize_folder_name
class PublishingService:
 def __init__(self,path:Path,provider):self.path=Path(path);self.provider=provider;self.repo=SQLitePublicationRepository(path);self.lifecycle=CaseLifecycleService(path)
 def eligibility(self,case_id,package_id):
  blockers=[]
  with closing(get_connection(self.path)) as c:
   case=c.execute('SELECT * FROM cases WHERE case_id=?',(case_id,)).fetchone();package=c.execute('SELECT * FROM content_packages WHERE content_package_id=?',(package_id,)).fetchone()
   if not case:blockers.append('case_missing')
   if not package:blockers.append('package_missing')
   if case and package and package['case_id']!=case_id:blockers.append('package_case_mismatch')
   if case and package and package['package_version']!=c.execute('SELECT MAX(package_version) FROM content_packages WHERE case_id=?',(case_id,)).fetchone()[0]:blockers.append('package_superseded')
   review=c.execute("SELECT * FROM quality_reviews WHERE content_package_id=? AND gate_status IN ('pass','pass_with_warnings') ORDER BY review_version DESC LIMIT 1",(package_id,)).fetchone() if package else None
   approval=c.execute("SELECT * FROM approval_events WHERE case_id=? AND draft_id=? AND new_status='approved' ORDER BY id DESC LIMIT 1",(case_id,package['draft_id'])).fetchone() if package else None
   if not review:blockers.append('quality_pass_required')
   if not approval or (case and case['approval_status']!='approved'):blockers.append('human_approval_required')
   return {'allowed':not blockers,'blockers':tuple(blockers),'case':case,'package':package,'review':review,'approval':approval}
 def publish(self,case_id,package,evidence,quality,approval,*,published_by,sharing=SharingPolicy(),run_id='publish',expected_package_version=None):
  check=self.eligibility(case_id,package.content_package_id)
  if not check['allowed']:raise DomainValidationError(f"Publishing blocked: {check['blockers']}")
  if expected_package_version is not None and package.package_version!=expected_package_version:raise VersionConflictError('Stale package version')
  key=hashlib.sha256(f'{case_id}|{package.content_package_id}|{package.package_version}'.encode()).hexdigest();existing=self.repo.by_key(key)
  if existing:return existing
  version=len(self.repo.history(case_id))+1;document=FinalDocumentMapper().map(self.lifecycle.cases._from_row(check['case']),package,evidence,quality,approval,version);now=datetime.now(UTC);op=f'op_{uuid4().hex}'
  with closing(get_connection(self.path)) as c:
   c.execute('BEGIN IMMEDIATE');c.execute('INSERT INTO external_operations(operation_id,operation_type,idempotency_key,state,created_at,updated_at) VALUES(?,?,?,?,?,?)',(op,'document_publish',key,'preparing',now.isoformat(),now.isoformat()));row=c.execute('SELECT * FROM cases WHERE case_id=?',(case_id,)).fetchone();case=self.lifecycle.cases._from_row(row);self.lifecycle._transition(c,case,WorkflowStatus.PUBLISHING,ActorType.EDITOR,published_by,'Publishing approved Content Package.',run_id,'publishing');c.commit()
  try:
   folder=('Ad Doctor','Approved Content',str(now.year),sanitize_folder_name(package.content_type));resource=self.provider.publish(document,DocumentBuilder().operations(document),folder,sharing,key)
   with closing(get_connection(self.path)) as c:c.execute("UPDATE external_operations SET state='external_document_created',external_resource_id=?,external_url=?,sharing_applied=?,updated_at=? WHERE operation_id=?",(resource['document_id'],resource['url'],1 if resource.get('sharing_applied') else 0,datetime.now(UTC).isoformat(),op));c.commit()
   current=self.repo.current(case_id)
   if current and current.google_document_id:self.provider.archive(current.google_document_id,('Ad Doctor','Published Archive',str(now.year)))
   with closing(get_connection(self.path)) as c:
    c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT * FROM cases WHERE case_id=?',(case_id,)).fetchone();case=self.lifecycle.cases._from_row(row)
    if current:c.execute("UPDATE publications SET publishing_status='superseded',updated_at=? WHERE publication_id=?",(now.isoformat(),current.publication_id))
    pub=Publication(f'publication_{uuid4().hex}',case_id,package.content_package_id,package.package_version,approval.approval_event_id,quality.quality_review_id,version,PublishingStatus.PUBLISHED,self.provider.provider_name,resource['document_id'],resource['url'],resource.get('folder_id'),resource.get('file_id'),document.title,document.checksum(),sharing.visibility,now,published_by,run_id,now,now,current.publication_id if current else None,{'template_version':'google_doc_v1'},key);self.repo.create_in(c,pub);case=self.lifecycle._transition(c,case,WorkflowStatus.PUBLISHED,ActorType.EDITOR,published_by,'Final document recorded.',run_id,'publishing');case=self.lifecycle._update_case(c,replace(case,publication_status='published'));c.execute("UPDATE external_operations SET state='completed',publication_id=?,updated_at=? WHERE operation_id=?",(pub.publication_id,datetime.now(UTC).isoformat(),op));c.commit();return pub
  except Exception as exc:
   with closing(get_connection(self.path)) as c:
    c.execute('BEGIN IMMEDIATE');c.execute("UPDATE external_operations SET state='compensation_required',error_type=?,updated_at=? WHERE operation_id=?",(type(exc).__name__,datetime.now(UTC).isoformat(),op));row=c.execute('SELECT * FROM cases WHERE case_id=?',(case_id,)).fetchone();case=self.lifecycle.cases._from_row(row)
    if case.workflow_status==WorkflowStatus.PUBLISHING.value:self.lifecycle._transition(c,case,WorkflowStatus.FAILED,ActorType.SYSTEM,'publishing_service',f'Publishing failed: {type(exc).__name__}',run_id,'publishing')
    c.commit()
   raise
