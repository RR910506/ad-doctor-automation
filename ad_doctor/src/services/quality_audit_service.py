from __future__ import annotations
import re
from contextlib import closing
from dataclasses import replace
from datetime import UTC,datetime
from pathlib import Path
from uuid import uuid4
from src.db import get_connection
from src.domain.content_package import ClaimClassification,ContentPackage,SlideType
from src.domain.quality_review import *
from src.domain.workflow import ActorType,WorkflowStatus
from src.repositories.sqlite_quality_review_repository import SQLiteQualityReviewRepository
from src.services.case_lifecycle_service import CaseLifecycleService
from src.services.voice_rules import find_pr_language
DIMENSIONS=('evidence_integrity','diagnosis_depth','strategic_reasoning','doctors_opinion_strength','prescription_usefulness','carousel_narrative','tone_and_voice','pr_language_avoidance','factual_traceability','readability_and_concision','fact_inference_opinion_distinction','content_completeness')
WEIGHTS={d:1/len(DIMENSIONS) for d in DIMENSIONS}
class QualityAuditService:
 def __init__(self,path:Path,strategic_provider=None):self.path=Path(path);self.strategic_provider=strategic_provider;self.repo=SQLiteQualityReviewRepository(path);self.lifecycle=CaseLifecycleService(path)
 def deterministic_findings(self,p:ContentPackage,evidence):
  findings=[]
  def add(sev,dim,section,msg,fix,blocking=True,slide=None,claims=()):findings.append(QualityFinding(f'finding_{uuid4().hex}',sev,dim,section,msg,fix,blocking,slide,tuple(claims)))
  if not p.doctors_opinion.strip():add(FindingSeverity.CRITICAL,'doctors_opinion_strength','doctors_opinion',"Doctor's Opinion is missing",'Add an evidence-connected independent opinion.')
  if not p.prescription.strip():add(FindingSeverity.CRITICAL,'prescription_usefulness','prescription','Prescription is missing','Add actionable marketing guidance.')
  sources={s.source_id for s in evidence.source_catalog};items={i.evidence_id for i in evidence.all_items()};contr={i.evidence_id for i in evidence.contradictions};results={i.evidence_id for i in evidence.results_evidence}
  for c in p.claims:
   if c.classification==ClaimClassification.VERIFIED_FACT and (not c.source_ids or not c.evidence_item_ids or set(c.source_ids)-sources or set(c.evidence_item_ids)-items):add(FindingSeverity.CRITICAL,'evidence_integrity',c.generated_section,'Unsupported verified fact','Link valid evidence and sources.',True,c.generated_slide,(c.claim_id,))
   if c.classification==ClaimClassification.VERIFIED_FACT and set(c.evidence_item_ids)&contr:add(FindingSeverity.CRITICAL,'evidence_integrity',c.generated_section,'Unresolved contradiction presented as fact','Disclose or resolve the contradiction.',True,c.generated_slide,(c.claim_id,))
   if c.generated_section=='results' and c.classification==ClaimClassification.VERIFIED_FACT and not set(c.evidence_item_ids)&results:add(FindingSeverity.ERROR,'factual_traceability','results','Result claim lacks result evidence','Remove or support the result claim.',True,c.generated_slide,(c.claim_id,))
  text=' '.join(str(x) for x in (p.title,p.diagnostic_hook,p.diagnosis,p.doctors_opinion,p.prescription,p.social_caption,p.article)).lower();matches=find_pr_language(text)
  for phrase in matches:add(FindingSeverity.ERROR,'pr_language_avoidance','package',f'PR-style language: {phrase}','Replace promotional exaggeration with analysis.',True)
  seen=set()
  for s in p.carousel:
   if not s.body.strip():add(FindingSeverity.ERROR,'carousel_narrative','carousel','Empty slide body','Add one clear idea.',True,s.slide_number)
   if s.character_count>700:add(FindingSeverity.WARNING,'readability_and_concision','carousel','Slide is excessively long','Shorten the slide.',False,s.slide_number)
   norm=re.sub(r'\W+',' ',s.body.lower()).strip()
   if norm in seen:add(FindingSeverity.WARNING,'carousel_narrative','carousel','Repeated slide content','Give each slide one distinct idea.',False,s.slide_number)
   seen.add(norm)
  return findings,matches
 def audit(self,p:ContentPackage,evidence,*,run_id='quality'):
  p.validate_against(evidence);findings,matches=self.deterministic_findings(p,evidence);scores={d:100.0 for d in DIMENSIONS}
  for f in findings:scores[f.dimension]=min(scores.get(f.dimension,100),30 if f.blocking else 75)
  if self.strategic_provider:
   result=self.strategic_provider.audit(package=p.to_dict(),evidence=evidence.to_dict())
   for k,v in result.get('dimension_scores',{}).items():
    if k in scores:scores[k]=max(0,min(100,float(v)))
  overall=round(sum(scores[d]*WEIGHTS[d] for d in DIMENSIONS),2);blocking=any(f.blocking for f in findings);warnings=any(f.severity==FindingSeverity.WARNING for f in findings)
  gate=GateStatus.FAIL if blocking else GateStatus.PASS_WITH_WARNINGS if warnings else GateStatus.PASS
  with closing(get_connection(self.path)) as c:
   c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT * FROM cases WHERE case_id=?',(p.case_id,)).fetchone()
   if not row:raise LookupError('Case not found')
   case=self.lifecycle.cases._from_row(row);review_version=int(c.execute('SELECT COALESCE(MAX(review_version),0)+1 FROM quality_reviews WHERE content_package_id=?',(p.content_package_id,)).fetchone()[0])
   review=QualityReview(f'quality_{uuid4().hex}',p.case_id,p.content_package_id,p.package_version,'deterministic_plus_provider' if self.strategic_provider else 'deterministic','phase3a',review_version,datetime.now(UTC),overall,gate,scores,tuple(findings),tuple(cid for f in findings for cid in f.claim_ids),(),matches,(),tuple(f.recommended_correction for f in findings if f.blocking),'Blocked by explainable findings.' if blocking else 'Ready for human editorial review.',{})
   if case.workflow_status in {WorkflowStatus.CONTENT_DRAFTED.value,WorkflowStatus.QUALITY_REVIEW_FAILED.value}:case=self.lifecycle._transition(c,case,WorkflowStatus.QUALITY_REVIEW_PENDING,ActorType.SYSTEM,'quality_auditor','Quality audit started.',run_id,'quality')
   target=WorkflowStatus.QUALITY_REVIEW_FAILED if gate==GateStatus.FAIL else WorkflowStatus.EDITORIAL_REVIEW_PENDING
   case=self.lifecycle._transition(c,case,target,ActorType.SYSTEM,'quality_auditor','Quality audit completed.',run_id,'quality');case=self.lifecycle._update_case(c,replace(case,quality_status=gate.value));self.repo.create_in(c,review);c.execute('UPDATE content_packages SET gate_status=?,overall_score=? WHERE content_package_id=?',(gate.value,overall,p.content_package_id));c.commit();return review
