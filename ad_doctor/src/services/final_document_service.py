from __future__ import annotations
import re
from src.domain.errors import DomainValidationError
from src.domain.publication import FinalDocument
class FinalDocumentMapper:
 def map(self,case,package,evidence,quality,approval,publication_version):
  if package.case_id!=case.case_id or evidence.case_id!=case.case_id or quality.content_package_id!=package.content_package_id or quality.package_version!=package.package_version or approval.case_id!=case.case_id or approval.draft_id!=package.draft_id:raise DomainValidationError('Final document ownership or version alignment failed')
  if quality.gate_status.value not in {'pass','pass_with_warnings'} or approval.new_status.value!='approved':raise DomainValidationError('Final document requires passing quality and current approval')
  clean=lambda x:re.sub(r'(?m)^#{1,6}\s*|\*\*|__|`','',str(x or '')).strip()
  return FinalDocument(clean(package.title),clean(package.diagnostic_hook),{'case_id':case.case_id,'content_type':package.content_type,'brand':case.brand or '','campaign':case.campaign or '','publication_version':str(publication_version)},clean(package.executive_diagnosis or package.diagnosis),f'{len(evidence.source_catalog)} approved source(s)',clean(package.symptoms),clean(package.patient_history),clean(package.diagnosis),clean(package.treatment),clean(package.results),clean(package.doctors_opinion),clean(package.prescription),tuple({'number':s.slide_number,'headline':clean(s.headline),'body':clean(s.body)} for s in package.carousel),clean(package.social_caption),clean(package.article),clean(package.design_direction),clean(quality.reviewer_summary),tuple(map(clean,package.uncertainties)),tuple(map(clean,package.required_disclosures)),tuple({'publication':s.publication,'title':s.title,'url':s.url} for s in evidence.source_catalog),{'package_version':str(package.package_version),'quality_score':str(quality.overall_score),'approval_event_id':approval.approval_event_id})
class DocumentBuilder:
 def operations(self,d):
  ops=[{'type':'title','text':'THE AD DOCTOR'},{'type':'heading1','text':d.title},{'type':'subtitle','text':d.subtitle}]
  for title,value in [('Executive Diagnosis',d.executive_diagnosis),('Source Coverage',d.source_coverage_summary),('Symptoms',d.symptoms),('Patient History',d.patient_history),('Diagnosis',d.diagnosis),('Treatment',d.treatment),('Results',d.results),("Doctor's Opinion",d.doctors_opinion),('Prescription',d.prescription)]:ops.extend(({'type':'heading2','text':title},{'type':'paragraph','text':value}))
  ops.append({'type':'heading2','text':'Carousel Content'})
  for s in d.carousel:ops.extend(({'type':'heading3','text':f"Slide {s['number']}: {s['headline']}"},{'type':'paragraph','text':s['body']}))
  ops.extend(({'type':'heading2','text':'Sources'},*({'type':'hyperlink','text':s['title'],'url':s['url']} for s in d.sources)))
  return ops
def sanitize_folder_name(value):return re.sub(r'[<>:"/\\|?*]+','-',value).strip(' .')[:80] or 'Unclassified'
