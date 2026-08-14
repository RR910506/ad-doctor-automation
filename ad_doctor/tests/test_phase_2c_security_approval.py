from __future__ import annotations
import os,sys,tempfile,unittest
from contextlib import closing
from pathlib import Path
from unittest import mock
PROJECT_DIR=Path(__file__).resolve().parents[1];sys.path.insert(0,str(PROJECT_DIR))
from src.db import get_connection,initialize_database
from src.domain.approval import *
from src.domain.case_file import CaseFile
from src.domain.errors import DomainValidationError,EntityNotFoundError
from src.repositories.sqlite_case_repository import SQLiteCaseRepository
from src.security.providers import *
from src.security.redaction import redact
from src.security.secret_audit import audit_secrets
from src.services.editorial_approval_service import EditorialApprovalService
from src.services.publishing_eligibility import *
from src.adapters.google_approval_status import map_google_approval_status
from src.migrations.case_file_foundation import apply_case_file_foundation

class SecretTests(unittest.TestCase):
 def test_environment_resolution_and_blanks(self):
  p=EnvironmentSecretProvider({'OPENAI_API_KEY':' key ','PERPLEXITY_API_KEY':'placeholder'})
  self.assertEqual(p.resolve('OPENAI_API_KEY').value,'key');self.assertFalse(p.resolve('PERPLEXITY_API_KEY').configured)
 def test_streamlit_and_priority(self):
  p=CompositeSecretProvider(EnvironmentSecretProvider({'OPENAI_API_KEY':'env'}),StreamlitSecretProvider({'OPENAI_API_KEY':'st'}))
  self.assertEqual(p.resolve('OPENAI_API_KEY').source,'environment:OPENAI_API_KEY')
 def test_legacy_warning_has_no_value(self):
  r=LegacySecretProvider(lambda k:'super-secret').resolve('OPENAI_API_KEY')
  self.assertNotIn('super-secret',r.warning);self.assertEqual(r.source,'legacy_sqlite')
 def test_redaction(self):
  self.assertEqual(redact({'api_key':'abc','safe':'yes'})['api_key'],'[REDACTED]');self.assertNotIn('abc',redact('api_key=abc failure'))
 def test_audit_has_status_not_value(self):
  items=audit_secrets(CompositeSecretProvider(EnvironmentSecretProvider({'OPENAI_API_KEY':'abc'})))
  self.assertTrue(next(x for x in items if x.key=='OPENAI_API_KEY').configured);self.assertFalse(any('abc' in str(x) for x in items))

class ApprovalTests(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();self.db=Path(self.t.name)/'a.db';initialize_database(self.db,PROJECT_DIR/'database'/'schema.sql')
  with closing(get_connection(self.db)) as c:
   c.execute("INSERT INTO topics(id,exact_topic,normalized_topic,status) VALUES(1,'Topic','topic','qualified')");lid=c.execute("INSERT INTO locked_topics(topic_id,run_id,locked_topic) VALUES(1,'r','Topic')").lastrowid;self.draft=c.execute("INSERT INTO content_outputs(locked_topic_id,content_type,draft_markdown) VALUES(?,'social','draft')",(lid,)).lastrowid;c.commit()
  self.case=SQLiteCaseRepository(self.db).create(CaseFile.for_topic(1,'Topic',workflow_status='content_drafted',qualification_status='qualified',draft_id=str(self.draft)))
  self.s=EditorialApprovalService(self.db)
 def tearDown(self):self.t.cleanup()
 def test_submit_and_approve_history_idempotent(self):
  a=self.s.decide(self.case.case_id,str(self.draft),ApprovalDecision.SUBMIT_FOR_REVIEW,reviewer_id='editor')
  same=self.s.decide(self.case.case_id,str(self.draft),ApprovalDecision.SUBMIT_FOR_REVIEW,reviewer_id='editor')
  b=self.s.decide(self.case.case_id,str(self.draft),ApprovalDecision.APPROVE,reviewer_id='editor')
  self.assertEqual(a.approval_event_id,same.approval_event_id);self.assertEqual(b.new_status,ApprovalStatus.APPROVED);self.assertEqual(len(self.s.events.history(self.case.case_id)),2)
 def test_missing_draft_rejected(self):
  with self.assertRaises(EntityNotFoundError):self.s.decide(self.case.case_id,'999',ApprovalDecision.SUBMIT_FOR_REVIEW,reviewer_id='e')
 def test_cannot_approve_before_review(self):
  with self.assertRaises(DomainValidationError):self.s.decide(self.case.case_id,str(self.draft),ApprovalDecision.APPROVE,reviewer_id='e')
 def test_revision_and_rejection_require_reason(self):
  self.s.decide(self.case.case_id,str(self.draft),ApprovalDecision.SUBMIT_FOR_REVIEW,reviewer_id='e')
  with self.assertRaises(DomainValidationError):self.s.decide(self.case.case_id,str(self.draft),ApprovalDecision.REQUEST_REVISION,reviewer_id='e')
 def test_revision_resubmission(self):
  self.s.decide(self.case.case_id,str(self.draft),ApprovalDecision.SUBMIT_FOR_REVIEW,reviewer_id='e');self.s.decide(self.case.case_id,str(self.draft),ApprovalDecision.REQUEST_REVISION,reviewer_id='e',corrections='Fix claims');e=self.s.decide(self.case.case_id,str(self.draft),ApprovalDecision.SUBMIT_FOR_REVIEW,reviewer_id='e');self.assertEqual(e.new_status,ApprovalStatus.PENDING_REVIEW)
 def test_atomic_failure_leaves_case(self):
  before=SQLiteCaseRepository(self.db).get_by_case_id(self.case.case_id)
  with self.assertRaises(DomainValidationError):self.s.decide(self.case.case_id,str(self.draft),ApprovalDecision.APPROVE,reviewer_id='e')
  after=SQLiteCaseRepository(self.db).get_by_case_id(self.case.case_id);self.assertEqual(before.version,after.version);self.assertEqual(self.s.events.history(self.case.case_id),[])
 def test_eligibility(self):
  case=self.case.with_changes(approval_status='approved',workflow_status='approved',publication_status='published')
  self.assertTrue(publishing_eligibility(case,True).allowed);self.assertFalse(email_eligibility(case,True,None).allowed);self.assertTrue(email_eligibility(case,True,'doc').allowed)
 def test_google_mapping(self):
  self.assertEqual(map_google_approval_status('approved'),ApprovalStatus.APPROVED)
  with self.assertRaises(ValueError):map_google_approval_status('mystery')
 def test_migration_idempotent(self):
  with closing(get_connection(self.db)) as c:apply_case_file_foundation(c);apply_case_file_foundation(c);self.assertIsNotNone(c.execute("SELECT name FROM sqlite_master WHERE name='approval_events'").fetchone())

if __name__=='__main__':unittest.main()
