from __future__ import annotations
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import NAMESPACE_URL, uuid5
from src.domain.errors import DomainValidationError
from src.domain.evidence_pack import EvidencePack

class ClaimClassification(StrEnum):
 VERIFIED_FACT='verified_fact';SOURCE_CLAIM='source_claim';STRATEGIC_INFERENCE='strategic_inference';DOCTOR_OPINION='doctor_opinion';UNKNOWN='unknown'
class SlideType(StrEnum):
 DIAGNOSTIC_HOOK='diagnostic_hook';SYMPTOMS='symptoms';PATIENT_HISTORY='patient_history';HIDDEN_PROBLEM='hidden_problem';DIAGNOSIS='diagnosis';TREATMENT='treatment';WHAT_WORKED='what_worked';RISK='risk';DOCTORS_OPINION='doctors_opinion';PRESCRIPTION='prescription';SOURCES='sources'
@dataclass(frozen=True,slots=True)
class ContentClaim:
 claim_id:str;statement:str;classification:ClaimClassification;evidence_item_ids:tuple[str,...]=();source_ids:tuple[str,...]=();confidence:float=0.0;generated_section:str='';generated_slide:int|None=None;verification_status:str='unverified';notes:str|None=None
 def __post_init__(self):
  if not self.claim_id.strip() or not self.statement.strip():raise DomainValidationError('Claim ID and statement are required')
  if not 0<=self.confidence<=1:raise DomainValidationError('Claim confidence must be between 0 and 1')
  if self.classification==ClaimClassification.VERIFIED_FACT and (not self.evidence_item_ids or not self.source_ids):raise DomainValidationError('Verified facts require evidence and sources')
  if self.classification==ClaimClassification.SOURCE_CLAIM and not self.source_ids:raise DomainValidationError('Source claims must identify a source')
  if self.classification==ClaimClassification.STRATEGIC_INFERENCE and not self.evidence_item_ids:raise DomainValidationError('Strategic inference requires supporting evidence')
  if self.classification==ClaimClassification.STRATEGIC_INFERENCE and not any(x in self.statement.lower() for x in ('appears','suggests','likely','indicates','may','inference')):raise DomainValidationError('Strategic inference must be written as qualified analysis')
  if self.classification==ClaimClassification.DOCTOR_OPINION and not self.evidence_item_ids:raise DomainValidationError("Doctor's Opinion must remain connected to evidence")
  if self.classification==ClaimClassification.UNKNOWN and not any(x in self.statement.lower() for x in ('unknown','uncertain','unconfirmed','not available')):raise DomainValidationError('Unknown claims must remain visibly uncertain')
@dataclass(frozen=True,slots=True)
class CarouselSlide:
 slide_number:int;slide_type:SlideType;headline:str;body:str;supporting_claim_ids:tuple[str,...]=();supporting_source_ids:tuple[str,...]=();visual_direction:str='';editor_note:str|None=None;character_count:int=0;reading_time_seconds:int=0
 def __post_init__(self):
  if self.slide_number<1 or not self.headline.strip() or not self.body.strip():raise DomainValidationError('Slides require number, headline and body')
  object.__setattr__(self,'character_count',len(self.headline)+len(self.body));object.__setattr__(self,'reading_time_seconds',max(1,round(len(self.body.split())/3.3)))
@dataclass(frozen=True,slots=True)
class ContentPackage:
 content_package_id:str;schema_version:str;package_version:int;case_id:str;evidence_pack_id:str;draft_id:str|None;content_type:str;generation_provider:str;generation_model:str;generated_at:datetime;created_at:datetime;updated_at:datetime
 symptoms:str;patient_history:str;diagnosis:str;treatment:str;results:str;prescription:str;doctors_opinion:str
 business_problem:str='';marketing_problem:str='';consumer_tension:str='';category_tension:str='';cultural_context:str='';strategic_rationale:str='';what_worked:str='';what_did_not_work:str='';risks:str='';missed_opportunities:str='';short_term_effect:str='';long_term_effect:str='';alternative_treatment:str='';final_verdict:str=''
 executive_diagnosis:str='';carousel:tuple[CarouselSlide,...]=();social_caption:str='';article:str='';design_direction:str='';source_notes:str='';claims:tuple[ContentClaim,...]=()
 title:str='';diagnostic_hook:str='';audience:str='marketers';tone:str='analytical';uncertainties:tuple[str,...]=();required_disclosures:tuple[str,...]=();editorial_notes:str='';parent_package_version:int|None=None;revision_reason:str|None=None;metadata:dict[str,Any]=field(default_factory=dict)
 def __post_init__(self):
  mandatory={'symptoms':self.symptoms,'patient_history':self.patient_history,'diagnosis':self.diagnosis,'treatment':self.treatment,'results':self.results,'prescription':self.prescription,'doctors_opinion':self.doctors_opinion}
  missing=[k for k,v in mandatory.items() if not str(v).strip()]
  if missing:raise DomainValidationError(f'Missing mandatory Content Package sections: {missing}')
  if self.package_version<1 or not self.case_id or not self.evidence_pack_id:raise DomainValidationError('Package version, Case and Evidence Pack are required')
  if self.content_type not in {'social','blog','carousel','article','unclassified'}:raise DomainValidationError(f'Invalid content type: {self.content_type}')
  numbers=[s.slide_number for s in self.carousel]
  if numbers and numbers!=list(range(1,len(numbers)+1)):raise DomainValidationError('Carousel slide numbers must be unique and sequential')
  types={s.slide_type for s in self.carousel}
  if self.carousel and (SlideType.DOCTORS_OPINION not in types or SlideType.PRESCRIPTION not in types):raise DomainValidationError("Carousel requires Doctor's Opinion and Prescription slides")
  claim_ids={c.claim_id for c in self.claims}
  for s in self.carousel:
   if set(s.supporting_claim_ids)-claim_ids:raise DomainValidationError(f'Slide {s.slide_number} references unknown claims')
 def validate_against(self,evidence:EvidencePack)->None:
  if evidence.evidence_pack_id!=self.evidence_pack_id or evidence.case_id!=self.case_id:raise DomainValidationError('Invalid Evidence Pack linkage')
  sources={s.source_id for s in evidence.source_catalog};items={i.evidence_id for i in evidence.all_items()};contradictions={i.evidence_id for i in evidence.contradictions}
  for c in self.claims:
   if set(c.source_ids)-sources:raise DomainValidationError(f'Claim {c.claim_id} references unknown source IDs')
   if set(c.evidence_item_ids)-items:raise DomainValidationError(f'Claim {c.claim_id} references unknown evidence IDs')
   if c.classification==ClaimClassification.VERIFIED_FACT and set(c.evidence_item_ids)&contradictions:raise DomainValidationError(f'Claim {c.claim_id} relies on unresolved contradiction')
  for s in self.carousel:
   if set(s.supporting_source_ids)-sources:raise DomainValidationError(f'Slide {s.slide_number} references unknown source IDs')
   facts=[c for c in self.claims if c.claim_id in s.supporting_claim_ids and c.classification in {ClaimClassification.VERIFIED_FACT,ClaimClassification.SOURCE_CLAIM}]
   if facts and not s.supporting_source_ids:raise DomainValidationError(f'Factual slide {s.slide_number} lacks source support')
 def to_dict(self):
  d=asdict(self);d['generated_at']=self.generated_at.isoformat();d['created_at']=self.created_at.isoformat();d['updated_at']=self.updated_at.isoformat();
  for c in d['claims']:c['classification']=c['classification'].value if hasattr(c['classification'],'value') else c['classification']
  for s in d['carousel']:s['slide_type']=s['slide_type'].value if hasattr(s['slide_type'],'value') else s['slide_type']
  return d
 @classmethod
 def from_dict(cls,p):
  q=dict(p);q['generated_at']=datetime.fromisoformat(q['generated_at']);q['created_at']=datetime.fromisoformat(q['created_at']);q['updated_at']=datetime.fromisoformat(q['updated_at']);q['claims']=tuple(ContentClaim(**{**x,'classification':ClaimClassification(x['classification']),'evidence_item_ids':tuple(x.get('evidence_item_ids',())),'source_ids':tuple(x.get('source_ids',()))}) for x in q.get('claims',()));q['carousel']=tuple(CarouselSlide(**{**x,'slide_type':SlideType(x['slide_type']),'supporting_claim_ids':tuple(x.get('supporting_claim_ids',())),'supporting_source_ids':tuple(x.get('supporting_source_ids',()))}) for x in q.get('carousel',()));q['uncertainties']=tuple(q.get('uncertainties',()));q['required_disclosures']=tuple(q.get('required_disclosures',()));return cls(**q)
 @classmethod
 def identity(cls,case_id,version):return f"package_{uuid5(NAMESPACE_URL,f'ad-doctor/package/{case_id}/{version}').hex}"
