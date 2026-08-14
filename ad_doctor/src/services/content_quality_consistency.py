from dataclasses import dataclass
from pathlib import Path
import json
from src.db import get_connection
from src.domain.content_package import ContentPackage,ClaimClassification
from src.domain.evidence_pack import EvidencePack
@dataclass(frozen=True,slots=True)
class ContentQualityIssue:issue_type:str;severity:str;case_id:str|None;content_package_id:str|None;explanation:str
def check_content_quality_consistency(path:Path):
 c=get_connection(path);issues=[]
 def add(t,s,case,pkg,msg):issues.append(ContentQualityIssue(t,s,case,pkg,msg))
 try:
  checks=[('case_draft_without_package',"SELECT c.case_id,NULL package_id FROM cases c WHERE c.draft_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM content_packages p WHERE p.case_id=c.case_id AND p.draft_id=c.draft_id)"),('package_missing_case',"SELECT p.case_id,p.content_package_id package_id FROM content_packages p LEFT JOIN cases c ON c.case_id=p.case_id WHERE c.case_id IS NULL"),('package_missing_evidence',"SELECT p.case_id,p.content_package_id package_id FROM content_packages p LEFT JOIN evidence_packs e ON e.evidence_pack_id=p.evidence_pack_id WHERE e.evidence_pack_id IS NULL"),('evidence_case_mismatch',"SELECT p.case_id,p.content_package_id package_id FROM content_packages p JOIN evidence_packs e ON e.evidence_pack_id=p.evidence_pack_id WHERE e.case_id!=p.case_id"),('passed_without_review',"SELECT p.case_id,p.content_package_id package_id FROM content_packages p WHERE p.gate_status IN ('pass','pass_with_warnings') AND NOT EXISTS(SELECT 1 FROM quality_reviews q WHERE q.content_package_id=p.content_package_id)"),('failed_review_case_editorial',"SELECT q.case_id,q.content_package_id package_id FROM quality_reviews q JOIN cases c ON c.case_id=q.case_id WHERE q.gate_status='fail' AND c.workflow_status='editorial_review_pending'"),('passed_review_case_pending',"SELECT q.case_id,q.content_package_id package_id FROM quality_reviews q JOIN cases c ON c.case_id=q.case_id WHERE q.gate_status IN ('pass','pass_with_warnings') AND c.workflow_status='quality_review_pending'"),('review_missing_package',"SELECT q.case_id,q.content_package_id package_id FROM quality_reviews q LEFT JOIN content_packages p ON p.content_package_id=q.content_package_id WHERE p.content_package_id IS NULL"),('review_package_version_mismatch',"SELECT q.case_id,q.content_package_id package_id FROM quality_reviews q JOIN content_packages p ON p.content_package_id=q.content_package_id WHERE q.package_version!=p.package_version"),('stale_review',"SELECT p.case_id,p.content_package_id package_id FROM content_packages p WHERE p.package_version<(SELECT MAX(x.package_version) FROM content_packages x WHERE x.case_id=p.case_id) AND EXISTS(SELECT 1 FROM quality_reviews q WHERE q.content_package_id=p.content_package_id)"),('multiple_current_packages',"SELECT case_id,MAX(content_package_id) package_id FROM content_packages GROUP BY case_id HAVING COUNT(*) FILTER(WHERE package_version=(SELECT MAX(x.package_version) FROM content_packages x WHERE x.case_id=content_packages.case_id))>1"),('current_package_older_than_draft',"SELECT c.case_id,p.content_package_id package_id FROM cases c JOIN content_packages p ON p.case_id=c.case_id WHERE p.package_version=(SELECT MAX(x.package_version) FROM content_packages x WHERE x.case_id=c.case_id) AND p.draft_id!=c.draft_id"),('approval_failed_quality',"SELECT c.case_id,p.content_package_id package_id FROM cases c JOIN content_packages p ON p.case_id=c.case_id WHERE c.approval_status='approved' AND p.gate_status='fail' AND p.package_version=(SELECT MAX(x.package_version) FROM content_packages x WHERE x.case_id=c.case_id)")]
  for kind,sql in checks:
   for r in c.execute(sql):add(kind,'error',r['case_id'],r['package_id'],kind.replace('_',' '))
  for r in c.execute('SELECT p.*,e.payload_json evidence_json FROM content_packages p LEFT JOIN evidence_packs e ON e.evidence_pack_id=p.evidence_pack_id'):
   try:raw=json.loads(r['payload_json'])
   except Exception:add('invalid_package_payload','error',r['case_id'],r['content_package_id'],'Package payload is not valid JSON');continue
   if not str(raw.get('doctors_opinion') or '').strip():add('missing_doctors_opinion','error',r['case_id'],r['content_package_id'],"Doctor's Opinion is missing")
   if not str(raw.get('prescription') or '').strip():add('missing_prescription','error',r['case_id'],r['content_package_id'],'Prescription is missing')
   if r['evidence_json']:
    try:
     evidence=EvidencePack.from_dict(json.loads(r['evidence_json']));package=ContentPackage.from_dict(raw);package.validate_against(evidence)
    except Exception as exc:add('invalid_package_traceability','error',r['case_id'],r['content_package_id'],f'Package traceability validation failed: {type(exc).__name__}')
  return issues
 finally:c.close()
