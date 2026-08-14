from __future__ import annotations
import hashlib,json,os,socket,sys,tempfile,unittest
from contextlib import closing
from dataclasses import replace
from datetime import UTC,datetime
from pathlib import Path
from unittest import mock
PROJECT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(PROJECT))
from src.db import get_connection,initialize_database
from src.domain.case_file import CaseFile
from src.domain.content_package import *
from src.domain.evidence_pack import *
from src.domain.errors import DomainValidationError
from src.domain.quality_review import GateStatus
from src.migrations.case_file_foundation import apply_case_file_foundation
from src.repositories.sqlite_case_repository import SQLiteCaseRepository
from src.repositories.sqlite_content_package_repository import SQLiteContentPackageRepository
from src.repositories.sqlite_evidence_repository import SQLiteEvidenceRepository
from src.services.ad_doctor_content_service import AdDoctorContentService
from src.services.claim_traceability_service import ClaimTraceabilityService
from src.services.content_quality_consistency import check_content_quality_consistency
from src.services.legacy_content_quality_shadow import LegacyContentQualityShadowService
from src.services.quality_audit_service import QualityAuditService
from src.services.structured_openai_content_provider import StructuredOpenAIContentProvider

class FakeProvider:
 provider_name='fake';model_name='fake-structured'
 def __init__(self,payload):self.payload=payload
 def generate(self,**kwargs):return self.payload
 def revise(self,*,package,target,instruction,evidence):
  p=dict(package);p['package_version']+=1;p['parent_package_version']=package['package_version'];p['revision_reason']=instruction;p['content_package_id']=ContentPackage.identity(p['case_id'],p['package_version']);p[target]='Revised '+str(p[target]);p['updated_at']=datetime.now(UTC).isoformat();return p
class FakeAudit:
 def audit(self,**kwargs):return {'dimension_scores':{'diagnosis_depth':88,'strategic_reasoning':91}}
class FakeOpenAIClient:
 def __init__(self,response):self.response=response;self.calls=[]
 def generate_structured(self,**kwargs):self.calls.append(kwargs);return self.response

class Phase3AFixture(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.db=Path(self.tmp.name)/'a.db';initialize_database(self.db,PROJECT/'database'/'schema.sql')
  with closing(get_connection(self.db)) as c:
   c.execute("INSERT INTO topics(id,exact_topic,normalized_topic,status) VALUES(1,'Brand move','brand move','qualified')");locked=c.execute("INSERT INTO locked_topics(topic_id,run_id,locked_topic) VALUES(1,'r','Brand move')").lastrowid;self.draft=c.execute("INSERT INTO content_outputs(locked_topic_id,content_type,draft_markdown) VALUES(?,'social','legacy draft')",(locked,)).lastrowid;c.commit()
  self.case=SQLiteCaseRepository(self.db).create(CaseFile.for_topic(1,'Brand move',workflow_status='content_drafted',qualification_status='qualified',draft_id=str(self.draft)))
  source=EvidenceSource('s1','Publication','Title','https://example.invalid/a',None,datetime.now(UTC).isoformat(),'publication',1,True)
  fact=EvidenceItem('e1','Brand launched the move.','event',('s1',),.9,VerificationStatus.VERIFIED)
  result=EvidenceItem('r1','Reported sales increased.','result',('s1',),.8,VerificationStatus.VERIFIED)
  self.evidence=EvidencePack('1.0','ep1',self.case.case_id,'Brand move','fake','fake',datetime.now(UTC),(source,),(fact,),results_evidence=(result,),research_summary='Brand launched the move.')
  SQLiteEvidenceRepository(self.db).create(self.evidence)
  with closing(get_connection(self.db)) as c:c.execute('UPDATE cases SET evidence_pack_id=? WHERE case_id=?',('ep1',self.case.case_id));c.commit()
  self.case=SQLiteCaseRepository(self.db).get_by_case_id(self.case.case_id)
 def tearDown(self):self.tmp.cleanup()
 def package(self,version=1,pr=False):
  now=datetime.now(UTC);claims=(ContentClaim('c1','Brand launched the move.',ClaimClassification.VERIFIED_FACT,('e1',),('s1',),.9,'symptoms',2,'verified'),ContentClaim('c2','This suggests an attention problem.',ClaimClassification.STRATEGIC_INFERENCE,('e1',),(),.7,'diagnosis',5),ContentClaim('c3','The move is strategically sound but familiar.',ClaimClassification.DOCTOR_OPINION,('e1',),(),.7,'doctors_opinion',9))
  types=(SlideType.DIAGNOSTIC_HOOK,SlideType.SYMPTOMS,SlideType.PATIENT_HISTORY,SlideType.HIDDEN_PROBLEM,SlideType.DIAGNOSIS,SlideType.TREATMENT,SlideType.WHAT_WORKED,SlideType.RISK,SlideType.DOCTORS_OPINION,SlideType.PRESCRIPTION)
  slides=tuple(CarouselSlide(i,t,t.value.replace('_',' ').title(),'Brand launched the move.' if i==2 else f'One strategic idea {i}.',('c1',) if i==2 else (),('s1',) if i==2 else ()) for i,t in enumerate(types,1))
  return ContentPackage(ContentPackage.identity(self.case.case_id,version),'1.0',version,self.case.case_id,'ep1',str(self.draft),'social','fake','fake',now,now,now,'Brand launched the move.','Category context.','The actual problem is attention.','The treatment creates salience.','Results remain uncertain.','Build distinctive memory, not only attention.','Strategically sound. Creatively familiar.',carousel=slides,claims=claims,title='Amazing campaign' if pr else 'A strategic diagnosis',diagnostic_hook='What problem is being treated?')

class DomainTraceabilityTests(Phase3AFixture):
 def test_valid_creation_serialization_and_stable_identity(self):
  p=self.package();p.validate_against(self.evidence);self.assertEqual(ContentPackage.from_dict(p.to_dict()),p);self.assertEqual(p.content_package_id,ContentPackage.identity(self.case.case_id,1))
 def test_mandatory_sections_and_carousel_validation(self):
  with self.assertRaises(DomainValidationError):replace(self.package(),doctors_opinion='')
  with self.assertRaises(DomainValidationError):replace(self.package(),prescription='')
  with self.assertRaises(DomainValidationError):replace(self.package(),diagnosis='')
  with self.assertRaises(DomainValidationError):replace(self.package(),carousel=(self.package().carousel[0],self.package().carousel[0]))
 def test_claim_validation_and_unknown_sources(self):
  with self.assertRaises(DomainValidationError):ContentClaim('x','fact',ClaimClassification.VERIFIED_FACT)
  bad=replace(self.package(),claims=(replace(self.package().claims[0],source_ids=('missing',)),),carousel=())
  with self.assertRaises(DomainValidationError):bad.validate_against(self.evidence)
 def test_traceability_queries(self):
  t=ClaimTraceabilityService(self.package(),self.evidence);self.assertEqual(t.evidence_for_claim('c1')[0].evidence_id,'e1');self.assertEqual(t.sections_for_source('s1'),['symptoms']);self.assertEqual(t.claims_for_slide(2)[0].claim_id,'c1')

class RepositoryEngineRevisionTests(Phase3AFixture):
 def test_repository_immutable_versions_and_revision(self):
  p=self.package();repo=SQLiteContentPackageRepository(self.db);repo.create(p);service=AdDoctorContentService(self.db,FakeProvider(p.to_dict()));revised=service.revise(p,self.evidence,'doctors_opinion','Take a sharper position');self.assertEqual(len(repo.history(self.case.case_id)),2);self.assertEqual(repo.history(self.case.case_id)[0],p);self.assertEqual(revised.parent_package_version,1);self.assertEqual(revised.diagnosis,p.diagnosis)
 def test_revision_reason_required(self):
  p=self.package();SQLiteContentPackageRepository(self.db).create(p)
  with self.assertRaises(DomainValidationError):AdDoctorContentService(self.db,FakeProvider(p.to_dict())).revise(p,self.evidence,'diagnosis',' ')
 def test_structured_provider_and_injection(self):
  p=self.package();generated=AdDoctorContentService(self.db,FakeProvider(p.to_dict())).generate(self.case,self.evidence);self.assertEqual(generated.content_package_id,p.content_package_id)
 def test_injected_openai_adapter_parses_json_without_live_call(self):
  p=self.package();client=FakeOpenAIClient(json.dumps(p.to_dict()));provider=StructuredOpenAIContentProvider(client,'fake-model');generated=AdDoctorContentService(self.db,provider).generate(self.case,self.evidence);self.assertEqual(generated,p);self.assertEqual(client.calls[0]['model'],'fake-model')
 def test_malformed_openai_output_rejected(self):
  with self.assertRaises(DomainValidationError):StructuredOpenAIContentProvider(FakeOpenAIClient('{bad')).generate(case={},evidence={},framework={},configuration={},prompts={})
 def test_malformed_or_wrong_link_provider_rejected_without_storage(self):
  raw=self.package().to_dict();raw['evidence_pack_id']='wrong'
  with self.assertRaises(DomainValidationError):AdDoctorContentService(self.db,FakeProvider(raw)).generate(self.case,self.evidence)
  self.assertIsNone(SQLiteContentPackageRepository(self.db).latest(self.case.case_id))

class QualityLifecycleTests(Phase3AFixture):
 def test_quality_pass_is_scored_and_stops_before_approval(self):
  p=self.package();SQLiteContentPackageRepository(self.db).create(p);r=QualityAuditService(self.db,FakeAudit()).audit(p,self.evidence);self.assertIn(r.gate_status,{GateStatus.PASS,GateStatus.PASS_WITH_WARNINGS});case=SQLiteCaseRepository(self.db).get_by_case_id(self.case.case_id);self.assertEqual(case.workflow_status,'editorial_review_pending');self.assertNotEqual(case.approval_status,'approved')
 def test_pr_language_forces_fail_and_findings_persist(self):
  p=self.package(pr=True);SQLiteContentPackageRepository(self.db).create(p);r=QualityAuditService(self.db).audit(p,self.evidence);self.assertEqual(r.gate_status,GateStatus.FAIL);self.assertTrue(r.pr_language_matches);self.assertEqual(SQLiteCaseRepository(self.db).get_by_case_id(self.case.case_id).workflow_status,'quality_review_failed')
 def test_failed_package_revision_can_be_reaudited(self):
  bad=self.package(pr=True);SQLiteContentPackageRepository(self.db).create(bad);QualityAuditService(self.db).audit(bad,self.evidence);good=replace(self.package(2),parent_package_version=1,revision_reason='Remove PR language');SQLiteContentPackageRepository(self.db).create(good);review=QualityAuditService(self.db).audit(good,self.evidence);self.assertNotEqual(review.gate_status,GateStatus.FAIL);self.assertEqual(SQLiteCaseRepository(self.db).get_by_case_id(self.case.case_id).workflow_status,'editorial_review_pending')
 def test_long_and_repeated_slides_warn(self):
  p=self.package();long=replace(p.carousel[0],body='word '*800);repeat=replace(p.carousel[1],body=long.body,supporting_claim_ids=(),supporting_source_ids=());p=replace(p,carousel=(long,repeat)+p.carousel[2:]);f,_=QualityAuditService(self.db).deterministic_findings(p,self.evidence);self.assertTrue(any(x.explanation=='Slide is excessively long' for x in f));self.assertTrue(any(x.explanation=='Repeated slide content' for x in f))

class ShadowMigrationConsistencyTests(Phase3AFixture):
 def test_off_writes_nothing_and_shadow_writes_package_review(self):
  with mock.patch.dict(os.environ,{'AD_DOCTOR_CASE_FILES_MODE':'off'},clear=False):self.assertIsNone(LegacyContentQualityShadowService(self.db).mirror(1,self.draft,'off'))
  self.assertIsNone(SQLiteContentPackageRepository(self.db).latest(self.case.case_id))
  with mock.patch.dict(os.environ,{'AD_DOCTOR_CASE_FILES_MODE':'shadow'},clear=False):p=LegacyContentQualityShadowService(self.db).mirror(1,self.draft,'shadow');same=LegacyContentQualityShadowService(self.db).mirror(1,self.draft,'shadow')
  self.assertIsNotNone(p);self.assertEqual(p.content_package_id,same.content_package_id)
 def test_shadow_failure_preserves_legacy_and_logs_safely(self):
  with mock.patch.dict(os.environ,{'AD_DOCTOR_CASE_FILES_MODE':'shadow'},clear=False),mock.patch('src.services.legacy_content_quality_shadow.QualityAuditService.audit',side_effect=RuntimeError('fail')):LegacyContentQualityShadowService(self.db).mirror(1,self.draft,'failure')
  with closing(get_connection(self.db)) as c:self.assertEqual(c.execute('SELECT draft_markdown FROM content_outputs WHERE id=?',(self.draft,)).fetchone()[0],'legacy draft');self.assertEqual(c.execute("SELECT COUNT(*) FROM run_logs WHERE phase='content_quality_shadow'").fetchone()[0],1)
 def test_consistency_checker_read_only(self):
  before=hashlib.sha256(self.db.read_bytes()).hexdigest();issues=check_content_quality_consistency(self.db);self.assertTrue(any(i.issue_type=='case_draft_without_package' for i in issues));self.assertEqual(before,hashlib.sha256(self.db.read_bytes()).hexdigest())
 def test_migration_twice_and_no_destructive_sql(self):
  sql=(PROJECT/'database'/'migrations'/'005_content_quality.sql').read_text().upper();self.assertFalse(any(x in sql for x in ('DROP TABLE','DELETE FROM','RENAME TABLE')))
  with closing(get_connection(self.db)) as c:apply_case_file_foundation(c);apply_case_file_foundation(c);self.assertIsNotNone(c.execute("SELECT 1 FROM sqlite_master WHERE name='content_packages'").fetchone())
 def test_external_call_sentinel(self):
  with mock.patch.object(socket,'create_connection',side_effect=AssertionError('network')),mock.patch('requests.sessions.Session.request',side_effect=AssertionError('http')):self.package().validate_against(self.evidence)

if __name__=='__main__':unittest.main()
