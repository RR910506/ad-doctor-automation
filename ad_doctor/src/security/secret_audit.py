from __future__ import annotations
from dataclasses import dataclass
from src.security.secret_provider import SECRET_KEYS
@dataclass(frozen=True,slots=True)
class SecretAuditItem:
    key:str; configured:bool; source:str; legacy_fallback:bool; remediation:str
def audit_secrets(provider)->list[SecretAuditItem]:
    items=[]
    for key in sorted(SECRET_KEYS):
        r=provider.resolve(key); legacy=r.source=="legacy_sqlite"
        items.append(SecretAuditItem(key,r.configured,r.source,legacy,"Move the secret to environment or Streamlit secrets and clear the legacy field during an approved migration." if legacy else "No action required." if r.configured else "Configure through environment or Streamlit secrets."))
    return items

