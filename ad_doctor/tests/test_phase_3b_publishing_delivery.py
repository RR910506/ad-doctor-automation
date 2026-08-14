from __future__ import annotations
import hashlib,html,json,os,socket,sys,tempfile,unittest
from contextlib import closing
from datetime import UTC,datetime
from pathlib import Path
from unittest import mock
PROJECT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(PROJECT))
from tests.test_phase_3a_content_quality import Phase3AFixture
from src.db import get_connection
from src.domain.approval import ApprovalDecision
from src.domain.publication import *
from src.domain.subscriber import *
from src.domain.delivery import *
from src.domain.errors import DomainValidationError,VersionConflictError
from src.repositories.sqlite_content_package_repository import SQLiteContentPackageRepository
from src.repositories.sqlite_publishing_repositories import *
from src.services.editorial_approval_service import EditorialApprovalService
from src.services.quality_audit_service import QualityAuditService
from src.services.document_provider import GoogleWorkspaceDocumentPublisher
from src.services.final_document_service import FinalDocumentMapper,DocumentBuilder,sanitize_folder_name
from src.services.publishing_service import PublishingService
from src.services.subscriber_service import SubscriberService,UnsubscribeTokenService
from src.services.email_delivery_service import EmailDeliveryService,EmailTemplateRenderer
from src.services.publishing_consistency import check_publishing_consistency,reconcile_external_operations
from src.services.controlled_publishing import ControlledPublishingFacade
from src.migrations.case_file_foundation import apply_case_file_foundation
class FakeDocs:
 def __init__(self):self.created=[];self.ops=[]
 def create_document(self,title):self.created.append(title);return {'id':f'doc{len(self.created)}'}
 def apply_operations(self,id,ops):self.ops=ops
 def view_url(self,id):return f'https://docs.example.invalid/{id}'
class FakeDrive:
 def __init__(self):self.folders={};self.shared=[]
 def ensure_folder(self,name,parent):self.folders.setdefault((parent,name),f'f{len(self.folders)+1}');return self.folders[(parent,name)]
 def move_file(self,id,parent):self.parent=parent
 def set_link_viewer(self,id):self.shared.append(('link',id))
 def add_reader(self,id,email):self.shared.append((email,id))
 def archive(self,*a):pass
class FakeEmail:
 provider_name='fake_email'
 def __init__(self,fail_for=()):self.sent=[];self.fail_for=set(fail_for)
 def send(self,recipient,subject,html_body,text_body,idempotency_key):
  self.sent.append((recipient,subject,html_body,text_body))
  if recipient in self.fail_for:
   exc=RuntimeError('provider detail secret');exc.retryable=True;raise exc
  return {'message_id':f'msg-{len(self.sent)}'}
class Phase3BFixture(Phase3AFixture):
 def setUp(self):
  super().setUp();self.package_obj=self.package();SQLiteContentPackageRepository(self.db).create(self.package_obj);self.quality=QualityAuditService(self.db).audit(self.package_obj,self.evidence)
  approvals=EditorialApprovalService(self.db);approvals.decide(self.case.case_id,str(self.draft),ApprovalDecision.SUBMIT_FOR_REVIEW,reviewer_id='editor');self.approval=approvals.decide(self.case.case_id,str(self.draft),ApprovalDecision.APPROVE,reviewer_id='editor');self.docs=FakeDocs();self.drive=FakeDrive();self.provider=GoogleWorkspaceDocumentPublisher(self.docs,self.drive)
 def publish(self,visibility=Visibility.LINK_VIEWER):return PublishingService(self.db,self.provider).publish(self.case.case_id,self.package_obj,self.evidence,self.quality,self.approval,published_by='editor',sharing=SharingPolicy(visibility,link_viewer_enabled=visibility==Visibility.LINK_VIEWER))
class PublicationDocumentTests(Phase3BFixture):
 def test_final_document_mapping_checksum_and_formatting(self):
  case=SQLiteCaseRepository(self.db).get_by_case_id(self.case.case_id);d=FinalDocumentMapper().map(case,self.package_obj,self.evidence,self.quality,self.approval,1);self.assertEqual(d.checksum(),d.checksum());ops=DocumentBuilder().operations(d);self.assertTrue(any(x['type']=='heading2' for x in ops));self.assertTrue(any(x['type']=='hyperlink' for x in ops));self.assertNotIn('**',json.dumps(ops))
 def test_ownership_mismatch_rejected(self):
  case=SQLiteCaseRepository(self.db).get_by_case_id(self.case.case_id)
  with self.assertRaises(DomainValidationError):FinalDocumentMapper().map(case,self.package_obj,self.evidence,self.quality,replace_approval(self.approval,draft_id='999'),1)
 def test_google_provider_folder_reuse_private_and_link_policy(self):
  self.assertEqual(SharingPolicy().visibility,Visibility.PRIVATE);self.assertEqual(sanitize_folder_name('bad/name:*'),'bad-name-')
  with self.assertRaises(DomainValidationError):SharingPolicy(Visibility.LINK_VIEWER)
  p=self.publish();self.assertEqual(len(self.docs.created),1);self.assertTrue(self.drive.shared);self.assertEqual(p.visibility,Visibility.LINK_VIEWER)
 def test_publish_idempotent_and_workflow(self):
  p=self.publish();same=self.publish();self.assertEqual(p.publication_id,same.publication_id);case=SQLiteCaseRepository(self.db).get_by_case_id(self.case.case_id);self.assertEqual(case.workflow_status,'published')
 def test_quality_and_approval_required(self):
  with closing(get_connection(self.db)) as c:c.execute("UPDATE cases SET approval_status='pending' WHERE case_id=?",(self.case.case_id,));c.commit()
  self.assertIn('human_approval_required',PublishingService(self.db,self.provider).eligibility(self.case.case_id,self.package_obj.content_package_id)['blockers'])
class SubscriberTests(Phase3BFixture):
 def test_subscriber_validation_consent_and_duplicate(self):
  s=SubscriberService(self.db).add('Person@Example.com','Person',ConsentStatus.CONFIRMED,'form');self.assertTrue(s.eligible());self.assertEqual(SubscriberService(self.db).add('person@example.com').subscriber_id,s.subscriber_id)
  with self.assertRaises(DomainValidationError):SubscriberService(self.db).add('bad-email')
  pending=SubscriberService(self.db).add('pending@example.com');self.assertFalse(pending.eligible())
 def test_pause_reactivate_version_and_suppression(self):
  svc=SubscriberService(self.db);s=svc.add('a@example.com',consent=ConsentStatus.CONFIRMED);paused=svc.change(s.subscriber_id,SubscriberStatus.PAUSED,expected_version=1);self.assertFalse(paused.eligible());active=svc.change(s.subscriber_id,SubscriberStatus.ACTIVE,expected_version=2);self.assertTrue(active.eligible())
  with self.assertRaises(VersionConflictError):svc.change(s.subscriber_id,SubscriberStatus.PAUSED,expected_version=1)
 def test_import_preview_duplicates_and_invalid(self):
  svc=SubscriberService(self.db);svc.add('exists@example.com');r=svc.preview_import([{'email':'new@example.com'},{'email':'exists@example.com'},{'email':'bad'}]);self.assertEqual((len(r['valid']),len(r['duplicates']),len(r['invalid'])),(1,1,1));self.assertNotIn('exists@example.com',json.dumps(r))
 def test_unsubscribe_hash_and_idempotency(self):
  svc=SubscriberService(self.db);s=svc.add('u@example.com',consent=ConsentStatus.CONFIRMED);tokens=UnsubscribeTokenService(self.db);token=tokens.issue(s.subscriber_id)
  with closing(get_connection(self.db)) as c:stored=c.execute('SELECT unsubscribe_token_hash FROM subscribers').fetchone()[0]
  self.assertNotEqual(stored,token);self.assertEqual(len(stored),64);first=tokens.unsubscribe(token);second=tokens.unsubscribe(token);self.assertEqual(first.subscriber_id,second.subscriber_id);self.assertFalse(second.eligible())
class DeliveryTests(Phase3BFixture):
 def test_template_escaping_and_fallback(self):
  h,t=EmailTemplateRenderer().render(name='<Admin>',title='<Title>',diagnosis='safe',document_url='https://example.invalid/doc');self.assertIn('&lt;Admin&gt;',h);self.assertIn('<Admin>',t);self.assertNotIn('<Admin>',h)
 def test_test_email_is_separate_and_idempotent(self):
  p=self.publish();provider=FakeEmail();svc=EmailDeliveryService(self.db,provider);a=svc.send_test(p.publication_id,'test@example.com','test-key');b=svc.send_test(p.publication_id,'test@example.com','test-key');self.assertEqual(a['delivery_type'],'test');self.assertEqual(len(provider.sent),1);self.assertNotEqual(SQLiteCaseRepository(self.db).get_by_case_id(self.case.case_id).workflow_status,'emailed')
 def test_distribution_success_individual_and_idempotent(self):
  p=self.publish();subs=SubscriberService(self.db);subs.add('one@example.com','One',ConsentStatus.CONFIRMED);subs.add('two@example.com','Two',ConsentStatus.CONFIRMED);provider=FakeEmail();svc=EmailDeliveryService(self.db,provider);r=svc.distribute(p.publication_id);again=svc.distribute(p.publication_id);self.assertEqual(r.status,DistributionStatus.COMPLETED);self.assertEqual(len(provider.sent),2);self.assertEqual(again.sent,2);self.assertEqual(SQLiteCaseRepository(self.db).get_by_case_id(self.case.case_id).workflow_status,'emailed')
 def test_partial_failure_retry_safe_error(self):
  p=self.publish();subs=SubscriberService(self.db);subs.add('ok@example.com',consent=ConsentStatus.CONFIRMED);subs.add('fail@example.com',consent=ConsentStatus.CONFIRMED);provider=FakeEmail({'fail@example.com'});r=EmailDeliveryService(self.db,provider).distribute(p.publication_id);self.assertEqual(r.status,DistributionStatus.COMPLETED_WITH_FAILURES)
  with closing(get_connection(self.db)) as c:row=c.execute("SELECT * FROM deliveries WHERE status='failed'").fetchone();self.assertEqual(row['safe_error_message'],'RuntimeError');self.assertNotIn('fail@example.com',json.dumps(dict(row)))
 def test_private_visibility_blocks_distribution(self):
  p=self.publish(Visibility.PRIVATE)
  with self.assertRaises(DomainValidationError):EmailDeliveryService(self.db,FakeEmail()).distribute(p.publication_id)
class MigrationConsistencyTests(Phase3BFixture):
 def test_migration_idempotent_nondestructive_no_raw_token(self):
  sql=(PROJECT/'database'/'migrations'/'006_publishing_delivery.sql').read_text().upper();self.assertFalse(any(x in sql for x in ('DROP TABLE','DELETE FROM','RAW_TOKEN')))
  with closing(get_connection(self.db)) as c:apply_case_file_foundation(c);apply_case_file_foundation(c);self.assertIsNotNone(c.execute("SELECT 1 FROM sqlite_master WHERE name='publications'").fetchone())
 def test_checker_read_only_and_reconciliation(self):
  with closing(get_connection(self.db)) as c:c.execute("INSERT INTO external_operations(operation_id,operation_type,idempotency_key,state,created_at,updated_at) VALUES('x','document_publish','x','compensation_required',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)");c.commit()
  before=hashlib.sha256(self.db.read_bytes()).hexdigest();issues=check_publishing_consistency(self.db);self.assertTrue(any(x.issue_type=='external_reconciliation_required' for x in issues));self.assertTrue(reconcile_external_operations(self.db));self.assertEqual(before,hashlib.sha256(self.db.read_bytes()).hexdigest())
 def test_external_call_sentinel(self):
  with mock.patch.object(socket,'create_connection',side_effect=AssertionError('network')),mock.patch('requests.sessions.Session.request',side_effect=AssertionError('http')):self.publish()
 def test_feature_off_and_shadow_fake_gate(self):
  service=PublishingService(self.db,self.provider);self.provider.is_fake=True;facade=ControlledPublishingFacade(service);args=(self.case.case_id,self.package_obj,self.evidence,self.quality,self.approval)
  with mock.patch.dict(os.environ,{'AD_DOCTOR_CASE_FILES_MODE':'off'},clear=False):self.assertFalse(facade.publish(*args,published_by='editor')['executed'])
  with mock.patch.dict(os.environ,{'AD_DOCTOR_CASE_FILES_MODE':'shadow'},clear=False):self.assertTrue(facade.publish(*args,published_by='editor',sharing=SharingPolicy(Visibility.LINK_VIEWER,link_viewer_enabled=True))['executed'])
def replace_approval(a,**changes):
 from dataclasses import replace
 return replace(a,**changes)
from src.repositories.sqlite_case_repository import SQLiteCaseRepository
if __name__=='__main__':unittest.main()
