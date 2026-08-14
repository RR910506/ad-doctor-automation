from __future__ import annotations
from dataclasses import asdict,dataclass,field
from datetime import datetime
from enum import StrEnum
from typing import Any
class GateStatus(StrEnum):PASS='pass';PASS_WITH_WARNINGS='pass_with_warnings';FAIL='fail';ERROR='error'
class FindingSeverity(StrEnum):INFO='info';WARNING='warning';ERROR='error';CRITICAL='critical'
@dataclass(frozen=True,slots=True)
class QualityFinding:
 finding_id:str;severity:FindingSeverity;dimension:str;section:str;explanation:str;recommended_correction:str;blocking:bool;slide_number:int|None=None;claim_ids:tuple[str,...]=()
@dataclass(frozen=True,slots=True)
class QualityReview:
 quality_review_id:str;case_id:str;content_package_id:str;package_version:int;auditor_provider:str;auditor_model:str;review_version:int;reviewed_at:datetime;overall_score:float;gate_status:GateStatus;dimension_scores:dict[str,float];findings:tuple[QualityFinding,...]=();unsupported_claim_ids:tuple[str,...]=();missing_sections:tuple[str,...]=();pr_language_matches:tuple[str,...]=();contradiction_flags:tuple[str,...]=();required_revisions:tuple[str,...]=();reviewer_summary:str='';metadata:dict[str,Any]=field(default_factory=dict)
 def to_dict(self):
  d=asdict(self);d['reviewed_at']=self.reviewed_at.isoformat();d['gate_status']=self.gate_status.value
  for f in d['findings']:f['severity']=f['severity'].value if hasattr(f['severity'],'value') else f['severity']
  return d
 @classmethod
 def from_dict(cls,p):
  q=dict(p);q['reviewed_at']=datetime.fromisoformat(q['reviewed_at']);q['gate_status']=GateStatus(q['gate_status']);q['findings']=tuple(QualityFinding(**{**x,'severity':FindingSeverity(x['severity']),'claim_ids':tuple(x.get('claim_ids',()))}) for x in q.get('findings',()));
  for k in ('unsupported_claim_ids','missing_sections','pr_language_matches','contradiction_flags','required_revisions'):q[k]=tuple(q.get(k,()))
  return cls(**q)
