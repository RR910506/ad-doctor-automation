from __future__ import annotations
import json,logging
from contextlib import closing
from datetime import UTC,datetime
from pathlib import Path
from src.case_files import get_case_files_mode
from src.db import get_connection,log_run_event
from src.domain.content_package import *
from src.repositories.sqlite_content_package_repository import SQLiteContentPackageRepository
from src.repositories.sqlite_evidence_repository import SQLiteEvidenceRepository
from src.services.quality_audit_service import QualityAuditService
LOGGER=logging.getLogger(__name__)
class LegacyContentQualityShadowService:
 def __init__(self,path:Path):self.path=Path(path)
 def mirror(self,topic_id:int,draft_id:int,run_id:str):
  if get_case_files_mode()=='off':return None
  package_id=None;case_id=None
  try:
   with closing(get_connection(self.path)) as c:
    row=c.execute('SELECT c.*,co.content_type,co.draft_markdown FROM cases c JOIN content_outputs co ON co.id=? WHERE c.topic_id=?',(draft_id,topic_id)).fetchone()
   if not row:raise LookupError('Case or legacy draft not found')
   case_id=row['case_id'];evidence=SQLiteEvidenceRepository(self.path).get_by_evidence_pack_id(row['evidence_pack_id'])
   if not evidence:raise LookupError('Validated Evidence Pack not found')
   repo=SQLiteContentPackageRepository(self.path);existing=repo.latest(case_id)
   if existing and existing.draft_id==str(draft_id):return existing
   version=(existing.package_version+1) if existing else 1;now=datetime.now(UTC);package_id=ContentPackage.identity(case_id,version)
   facts=evidence.verified_facts;claims=tuple(ContentClaim(f'claim_{i+1}',x.statement,ClaimClassification.VERIFIED_FACT,(x.evidence_id,),x.source_ids,x.confidence,'symptoms',2,'verified') for i,x in enumerate(facts[:3]))
   src=tuple(sorted({s for c in claims for s in c.source_ids}));claim_ids=tuple(c.claim_id for c in claims)
   slides=(CarouselSlide(1,SlideType.DIAGNOSTIC_HOOK,'The diagnosis behind the update','What looks like news is a signal of a marketing problem.'),CarouselSlide(2,SlideType.SYMPTOMS,'Symptoms',evidence.research_summary or evidence.canonical_topic,claim_ids,src),CarouselSlide(3,SlideType.PATIENT_HISTORY,'Patient history','The category and consumer context shape the response.'),CarouselSlide(4,SlideType.HIDDEN_PROBLEM,'Hidden problem','The underlying challenge appears to be attention without guaranteed differentiation.'),CarouselSlide(5,SlideType.DIAGNOSIS,'Diagnosis','The strategic issue is not the announcement itself, but the problem it is trying to solve.'),CarouselSlide(6,SlideType.TREATMENT,'Treatment','The brand treatment converts that pressure into a visible marketing move.'),CarouselSlide(7,SlideType.WHAT_WORKED,'What worked','The move creates a clear short-term signal.'),CarouselSlide(8,SlideType.RISK,'Risk','Long-term distinctiveness remains uncertain.'),CarouselSlide(9,SlideType.DOCTORS_OPINION,"Doctor's Opinion",'Strategically plausible. Its durable brand value still needs evidence.'),CarouselSlide(10,SlideType.PRESCRIPTION,'Prescription','Marketers should connect attention to a distinctive memory structure and measurable result.'))
   package=ContentPackage(package_id,'1.0',version,case_id,evidence.evidence_pack_id,str(draft_id),row['content_type'],'legacy_shadow_mapper','phase3a',now,now,now,evidence.canonical_topic,'Available brand and category context from the Evidence Pack.','The move responds to an underlying marketing pressure.','The campaign action is the treatment.','No confirmed result is available unless the Evidence Pack contains one.','Tie short-term attention to a distinctive long-term brand asset.',"Strategically plausible, but durable brand value remains uncertain.",carousel=slides,claims=claims,title=evidence.canonical_topic,diagnostic_hook='What problem is this marketing move actually treating?',article=row['draft_markdown'],metadata={'prompt_versions':{'legacy_mapper':'1.0'},'run_id':run_id})
   package.validate_against(evidence);repo.create(package);QualityAuditService(self.path).audit(package,evidence,run_id=run_id);return package
  except Exception as exc:
   payload={'case_id':case_id,'topic_id':topic_id,'draft_id':draft_id,'stage':'content_quality_shadow','content_package_id':package_id,'error_type':type(exc).__name__,'run_id':run_id}
   LOGGER.error('Content quality shadow failed: %s',json.dumps(payload,sort_keys=True))
   with closing(get_connection(self.path)) as c:log_run_event(c,run_id,'content_quality_shadow','error','Legacy draft preserved; structured content shadow failed.',json.dumps(payload));c.commit()
   return None
