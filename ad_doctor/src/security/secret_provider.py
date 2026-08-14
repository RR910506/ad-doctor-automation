from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol

SECRET_KEYS=frozenset({"PERPLEXITY_API_KEY","OPENAI_API_KEY","GOOGLE_SERVICE_ACCOUNT_JSON","GOOGLE_SERVICE_ACCOUNT_FILE","SMTP_HOST","SMTP_PORT","SMTP_USERNAME","SMTP_PASSWORD","SMTP_FROM_EMAIL"})
PLACEHOLDERS={"changeme","change-me","placeholder","your-key","your_api_key","none","null"}

def valid_secret(value: object)->bool:
    text=str(value or "").strip()
    return bool(text) and text.lower() not in PLACEHOLDERS

@dataclass(frozen=True,slots=True)
class SecretResult:
    key:str; configured:bool; source:str; value:str|None=None; warning:str|None=None
    def redacted(self)->dict[str,object]: return {"key":self.key,"configured":self.configured,"source":self.source,"warning":self.warning}

class SecretProvider(Protocol):
    def resolve(self,key:str)->SecretResult: ...

class MissingSecretError(RuntimeError): pass

