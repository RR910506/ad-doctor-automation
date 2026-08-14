from __future__ import annotations
import hashlib,hmac,json,secrets
from contextlib import closing
from dataclasses import replace
from datetime import UTC,datetime
from pathlib import Path
from uuid import uuid4
from src.db import get_connection
from src.domain.errors import DomainValidationError,VersionConflictError
from src.domain.subscriber import *
from src.repositories.sqlite_publishing_repositories import SQLiteSubscriberRepository
class SubscriberService:
 def __init__(self,path:Path,strict_consent=True):self.path=Path(path);self.strict=strict_consent;self.repo=SQLiteSubscriberRepository(path)
 def add(self,email,name=None,consent=ConsentStatus.PENDING,source=None):
  normalized=email.strip().lower();existing=self.repo.by_email(normalized)
  if existing:return existing
  now=datetime.now(UTC);s=Subscriber(f'subscriber_{uuid4().hex}',email.strip(),normalized,name,SubscriberStatus.ACTIVE,consent,source,now if consent==ConsentStatus.CONFIRMED else None,(),'weekly',None,'en','unverified',None,None,None,now,now,{},1);return self.repo.create(s)
 def change(self,sid,status,reason=None,expected_version=None):
  s=self.repo.get(sid)
  if not s:raise LookupError('Subscriber not found')
  if expected_version is not None and s.version!=expected_version:raise VersionConflictError('Stale subscriber version')
  if status==SubscriberStatus.ACTIVE and s.consent_status in {ConsentStatus.WITHDRAWN,ConsentStatus.PENDING}:raise DomainValidationError('Subscriber cannot reactivate without acceptable consent')
  updated=replace(s,status=status,suppression_reason=reason,updated_at=datetime.now(UTC),version=s.version+1)
  with closing(get_connection(self.path)) as c:
   cursor=c.execute('UPDATE subscribers SET status=?,suppression_reason=?,updated_at=?,version=? WHERE subscriber_id=? AND version=?',(status.value,reason,updated.updated_at.isoformat(),updated.version,sid,s.version))
   if cursor.rowcount!=1:raise VersionConflictError('Stale subscriber version')
   c.execute('INSERT INTO subscriber_events(event_id,subscriber_id,event_type,occurred_at,metadata_json) VALUES(?,?,?,?,?)',(f'event_{uuid4().hex}',sid,status.value,datetime.now(UTC).isoformat(),json.dumps({'reason':reason})));c.commit()
  return updated
 def eligible(self):return self.repo.eligible(self.strict)
 def preview_import(self,rows):
  valid=[];invalid=[];duplicates=[];seen=set()
  for i,row in enumerate(rows,1):
   email=str(row.get('email') or '').strip().lower()
   try:temp=Subscriber('preview',email,email,row.get('name'),SubscriberStatus.ACTIVE,ConsentStatus(str(row.get('consent_status') or 'legacy_unknown')),str(row.get('consent_source') or '') or None,None,(),'weekly',row.get('segment'),'en','unverified',None,None,None,datetime.now(UTC),datetime.now(UTC),{},1)
   except Exception as exc:invalid.append({'row':i,'error':type(exc).__name__});continue
   if email in seen or self.repo.by_email(email):duplicates.append({'row':i,'masked':temp.masked_email()})
   else:seen.add(email);valid.append(row)
  return {'valid':valid,'invalid':invalid,'duplicates':duplicates}
class UnsubscribeTokenService:
 def __init__(self,path:Path):self.path=Path(path);self.service=SubscriberService(path)
 @staticmethod
 def digest(token):return hashlib.sha256(token.encode()).hexdigest()
 def issue(self,sid):
  token=secrets.token_urlsafe(32);hashed=self.digest(token)
  with closing(get_connection(self.path)) as c:c.execute('UPDATE subscribers SET unsubscribe_token_hash=?,updated_at=CURRENT_TIMESTAMP WHERE subscriber_id=?',(hashed,sid));c.commit()
  return token
 def unsubscribe(self,token):
  hashed=self.digest(token)
  with closing(get_connection(self.path)) as c:rows=c.execute('SELECT subscriber_id,unsubscribe_token_hash,status FROM subscribers WHERE unsubscribe_token_hash IS NOT NULL').fetchall()
  row=next((r for r in rows if hmac.compare_digest(r['unsubscribe_token_hash'],hashed)),None)
  if not row:raise DomainValidationError('Invalid unsubscribe token')
  if row['status']=='unsubscribed':return self.service.repo.get(row['subscriber_id'])
  return self.service.change(row['subscriber_id'],SubscriberStatus.UNSUBSCRIBED,'one_click_unsubscribe')
