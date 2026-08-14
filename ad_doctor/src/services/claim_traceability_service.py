from src.domain.content_package import ClaimClassification,ContentPackage
from src.domain.evidence_pack import EvidencePack
class ClaimTraceabilityService:
 def __init__(self,package:ContentPackage,evidence:EvidencePack):self.package=package;self.evidence=evidence
 def evidence_for_claim(self,claim_id):
  claim=next((c for c in self.package.claims if c.claim_id==claim_id),None);ids=set(claim.evidence_item_ids if claim else ());return [i for i in self.evidence.all_items() if i.evidence_id in ids]
 def sections_for_source(self,source_id):return sorted({c.generated_section for c in self.package.claims if source_id in c.source_ids})
 def claims_for_slide(self,n):return [c for c in self.package.claims if c.generated_slide==n or any(s.slide_number==n and c.claim_id in s.supporting_claim_ids for s in self.package.carousel)]
 def unsupported_slides(self):return [s.slide_number for s in self.package.carousel if any(c.classification in {ClaimClassification.VERIFIED_FACT,ClaimClassification.SOURCE_CLAIM} for c in self.claims_for_slide(s.slide_number)) and not s.supporting_source_ids]
 def unsupported_verified_facts(self):return [c.claim_id for c in self.package.claims if c.classification==ClaimClassification.VERIFIED_FACT and (not c.source_ids or not c.evidence_item_ids)]
 def contradictions_affecting_opinion(self):
  contradictions={i.evidence_id for i in self.evidence.contradictions};return [c.claim_id for c in self.package.claims if c.classification==ClaimClassification.DOCTOR_OPINION and set(c.evidence_item_ids)&contradictions]
