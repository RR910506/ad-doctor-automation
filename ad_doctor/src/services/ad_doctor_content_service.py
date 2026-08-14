from __future__ import annotations
from dataclasses import replace
from datetime import UTC,datetime
from pathlib import Path
from typing import Protocol
from src.domain.content_package import ContentPackage
from src.domain.errors import DomainValidationError
from src.domain.evidence_pack import EvidencePack
from src.repositories.sqlite_content_package_repository import SQLiteContentPackageRepository
from src.services.prompt_loader import PromptLoader
class StructuredContentProvider(Protocol):
 provider_name:str;model_name:str
 def generate(self,*,case,evidence:dict,framework:dict,configuration:dict,prompts:dict)->dict:...
 def revise(self,*,package:dict,target:str,instruction:str,evidence:dict)->dict:...
class AdDoctorContentService:
 def __init__(self,path:Path,provider:StructuredContentProvider,prompts:PromptLoader|None=None):self.path=Path(path);self.provider=provider;self.prompts=prompts or PromptLoader();self.repo=SQLiteContentPackageRepository(path)
 def generate(self,case,evidence:EvidencePack,framework:dict|None=None,configuration:dict|None=None):
  if evidence.case_id!=case.case_id:raise DomainValidationError('Validated Evidence Pack must belong to the Case')
  names=('diagnosis_system','opinion_system','prescription_system','carousel_system');prompts={n:self.prompts.load(n) for n in names}
  raw=self.provider.generate(case=case.to_dict(),evidence=evidence.to_dict(),framework=framework or {},configuration=configuration or {},prompts=prompts)
  package=ContentPackage.from_dict(raw);package.validate_against(evidence)
  if package.case_id!=case.case_id:raise DomainValidationError('Provider returned a package for another Case')
  self.repo.create(package);return package
 def revise(self,package:ContentPackage,evidence:EvidencePack,target:str,instruction:str):
  if not instruction.strip():raise DomainValidationError('Revision reason is required')
  raw=self.provider.revise(package=package.to_dict(),target=target,instruction=instruction,evidence=evidence.to_dict())
  revised=ContentPackage.from_dict(raw)
  if revised.package_version!=package.package_version+1 or revised.parent_package_version!=package.package_version:raise DomainValidationError('Revision must create the next immutable package version')
  if revised.revision_reason!=instruction:raise DomainValidationError('Revision reason was not preserved')
  revised.validate_against(evidence);self.repo.create(revised);return revised
 def revise_section(self,package,evidence,section,instruction):
  allowed={'diagnostic_hook','symptoms','patient_history','diagnosis','treatment','results','doctors_opinion','prescription','social_caption','article'}
  if section not in allowed:raise DomainValidationError(f'Unsupported revision section: {section}')
  return self.revise(package,evidence,section,instruction)
 def revise_slide(self,package,evidence,slide_number,instruction):
  if slide_number not in {s.slide_number for s in package.carousel}:raise DomainValidationError('Carousel slide does not exist')
  return self.revise(package,evidence,f'carousel_slide_{slide_number}',instruction)
