from __future__ import annotations
import re
SENSITIVE=re.compile(r"(api[_-]?key|token|secret|password|authorization|credential|service_account)",re.I)
def redact(value:object)->object:
    if isinstance(value,dict): return {k:("[REDACTED]" if SENSITIVE.search(str(k)) else redact(v)) for k,v in value.items()}
    if isinstance(value,(list,tuple)): return type(value)(redact(v) for v in value)
    text=str(value)
    text=re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+\-/=]+",r"\1[REDACTED]",text)
    text=re.sub(r"(?i)((?:api[_-]?key|password|token|secret)\s*[:=]\s*)[^\s,;]+",r"\1[REDACTED]",text)
    return text

