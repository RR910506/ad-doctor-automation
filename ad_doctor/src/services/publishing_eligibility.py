from dataclasses import dataclass
@dataclass(frozen=True,slots=True)
class EligibilityResult: allowed:bool; blockers:tuple[str,...]
def publishing_eligibility(case,current_draft_exists:bool,final_document_reference:str|None=None,*,draft_linked:bool=True,newer_unreviewed_draft:bool=False)->EligibilityResult:
 b=[]
 if not case:b.append('case_missing')
 else:
  if not current_draft_exists or not case.draft_id:b.append('current_draft_missing')
  if not draft_linked:b.append('draft_not_linked')
  if newer_unreviewed_draft:b.append('newer_unreviewed_draft')
  if case.approval_status!='approved':b.append('approval_required')
  if case.workflow_status not in {'approved','publishing','published','email_pending','emailed'}:b.append('workflow_not_approved')
 return EligibilityResult(not b,tuple(b))
def email_eligibility(case,current_draft_exists:bool,final_document_reference:str|None)->EligibilityResult:
 p=publishing_eligibility(case,current_draft_exists,final_document_reference);b=list(p.blockers)
 if case and case.publication_status!='published':b.append('publication_required')
 if not final_document_reference:b.append('final_document_required')
 return EligibilityResult(not b,tuple(b))
