from __future__ import annotations
import json
from src.domain.errors import DomainValidationError
class StructuredOpenAIContentProvider:
 """Injected OpenAI-compatible structured provider; owns no credentials or network client."""
 provider_name='openai'
 def __init__(self,client,model='gpt-4.1-mini'):self.client=client;self.model_name=model
 def generate(self,*,case,evidence,framework,configuration,prompts):
  response=self.client.generate_structured(model=self.model_name,system_prompts=prompts,input={'case':case,'evidence_pack':evidence,'framework':framework,'configuration':configuration})
  return self._parse(response)
 def revise(self,*,package,target,instruction,evidence):
  response=self.client.generate_structured(model=self.model_name,system_prompts={'revision':'Revise only the requested target and preserve structured traceability.'},input={'package':package,'target':target,'instruction':instruction,'evidence_pack':evidence})
  return self._parse(response)
 @staticmethod
 def _parse(value):
  try:return json.loads(value) if isinstance(value,str) else dict(value)
  except (ValueError,TypeError) as exc:raise DomainValidationError(f'Malformed structured provider output: {type(exc).__name__}') from exc
