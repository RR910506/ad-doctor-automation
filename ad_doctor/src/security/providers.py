from __future__ import annotations
import os
from collections.abc import Callable,Mapping
from src.security.secret_provider import SECRET_KEYS,SecretResult,valid_secret

ENV_MAP={"SMTP_HOST":"AD_DOCTOR_SMTP_HOST","SMTP_PORT":"AD_DOCTOR_SMTP_PORT","SMTP_USERNAME":"AD_DOCTOR_SMTP_USERNAME","SMTP_PASSWORD":"AD_DOCTOR_SMTP_PASSWORD","SMTP_FROM_EMAIL":"AD_DOCTOR_SMTP_FROM"}

class EnvironmentSecretProvider:
    def __init__(self,environ:Mapping[str,str]|None=None): self.environ=environ if environ is not None else os.environ
    def resolve(self,key:str)->SecretResult:
        env_key=ENV_MAP.get(key,key); value=self.environ.get(env_key)
        return SecretResult(key,valid_secret(value),f"environment:{env_key}",str(value).strip() if valid_secret(value) else None)

class StreamlitSecretProvider:
    def __init__(self,secrets:Mapping[str,object]): self.secrets=secrets
    def resolve(self,key:str)->SecretResult:
        value=self.secrets.get(key) or self.secrets.get(ENV_MAP.get(key,key))
        return SecretResult(key,valid_secret(value),"streamlit_secrets",str(value).strip() if valid_secret(value) else None)

class LegacySecretProvider:
    def __init__(self,loader:Callable[[str],object|None]): self.loader=loader
    def resolve(self,key:str)->SecretResult:
        value=self.loader(key); configured=valid_secret(value)
        warning=f"Legacy plaintext fallback used for {key} from sqlite." if configured else None
        return SecretResult(key,configured,"legacy_sqlite",str(value).strip() if configured else None,warning)

class CompositeSecretProvider:
    def __init__(self,*providers): self.providers=providers
    def resolve(self,key:str)->SecretResult:
        if key not in SECRET_KEYS: raise KeyError(f"Unsupported logical secret: {key}")
        for provider in self.providers:
            result=provider.resolve(key)
            if result.configured:return result
        return SecretResult(key,False,"not_configured")

