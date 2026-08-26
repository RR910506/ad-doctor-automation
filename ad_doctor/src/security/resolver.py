from __future__ import annotations
from contextlib import closing
from pathlib import Path
try:
 from dotenv import load_dotenv
except ImportError:
 load_dotenv=None
from src.config import DATABASE_PATH
from src.db import get_connection
from src.security.providers import CompositeSecretProvider,EnvironmentSecretProvider,LegacySecretProvider,StreamlitSecretProvider
from src.security.secret_provider import SecretResult, valid_secret

_RUNTIME_SECRETS: dict[tuple[str, str], str] = {}
PROJECT_DOTENV_PATH=Path(__file__).resolve().parents[2]/'.env'

class ResolvedEnvironmentSecretProvider(EnvironmentSecretProvider):
 def resolve(self,key:str)->SecretResult:
  result=super().resolve(key)
  return SecretResult(result.key,result.configured,'secure_environment',result.value,result.warning)

def load_project_dotenv(dotenv_path:Path|None=None)->None:
 path=Path(dotenv_path) if dotenv_path is not None else PROJECT_DOTENV_PATH
 if load_dotenv is not None:load_dotenv(dotenv_path=path,override=False)

def configure_runtime_secret(database_path:Path,key:str,value:object)->None:
 normalized=str(Path(database_path).resolve())
 text=str(value or '').strip()
 if valid_secret(text):_RUNTIME_SECRETS[(normalized,key)]=text
 else:_RUNTIME_SECRETS.pop((normalized,key),None)

class RuntimeSecretProvider:
 def __init__(self,database_path:Path):self.database_path=str(Path(database_path).resolve())
 def resolve(self,key:str)->SecretResult:
  value=_RUNTIME_SECRETS.get((self.database_path,key))
  return SecretResult(key,valid_secret(value),'runtime_memory',value if valid_secret(value) else None)
def build_secret_provider(database_path:Path=DATABASE_PATH,streamlit_secrets=None,*,dotenv_path:Path|None=None):
 load_project_dotenv(dotenv_path)
 def legacy(key):
  mapping={'PERPLEXITY_API_KEY':('research_provider_settings','api_key'),'OPENAI_API_KEY':('content_writer_settings','api_key'),'SMTP_HOST':('email_settings','smtp_host'),'SMTP_PORT':('email_settings','smtp_port'),'SMTP_USERNAME':('email_settings','smtp_username'),'SMTP_PASSWORD':('email_settings','smtp_password'),'SMTP_FROM_EMAIL':('email_settings','smtp_from')}
  if key not in mapping:return None
  table,column=mapping[key]
  with closing(get_connection(database_path)) as c:
   try:r=c.execute(f'SELECT {column} FROM {table} WHERE id=1').fetchone()
   except Exception:return None
  return r[0] if r else None
 providers=[ResolvedEnvironmentSecretProvider()]
 if streamlit_secrets is not None:providers.append(StreamlitSecretProvider(streamlit_secrets))
 providers.append(RuntimeSecretProvider(database_path))
 providers.append(LegacySecretProvider(legacy));return CompositeSecretProvider(*providers)
