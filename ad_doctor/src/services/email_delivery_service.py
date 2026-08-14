from __future__ import annotations
import hashlib,html,re
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC,datetime,timedelta
from pathlib import Path
from uuid import uuid4
from src.db import get_connection
from src.domain.delivery import *
from src.domain.errors import DomainValidationError
from src.domain.publication import PublishingStatus,Visibility
from src.repositories.sqlite_publishing_repositories import SQLiteDeliveryRepository,SQLitePublicationRepository,SQLiteSubscriberRepository
from src.services.case_lifecycle_service import CaseLifecycleService
from src.domain.workflow import ActorType,WorkflowStatus
from dataclasses import replace
class EmailProvider:
 provider_name='abstract'
 def send(self,recipient,subject,html_body,text_body,idempotency_key):raise NotImplementedError
class GmailEmailProvider(EmailProvider):
 """Optional contract placeholder; inject an implementation when Gmail delivery is approved."""
 provider_name='gmail'
class SmtpEmailProvider(EmailProvider):
 provider_name='smtp'
 def __init__(self,secret_provider,use_tls=True):self.secrets=secret_provider;self.use_tls=use_tls
 def send(self,recipient,subject,html_body,text_body,idempotency_key):
  import smtplib
  from email.message import EmailMessage
  host=self.secrets.resolve('SMTP_HOST').value;port=int(self.secrets.resolve('SMTP_PORT').value or 587);user=self.secrets.resolve('SMTP_USERNAME').value;password=self.secrets.resolve('SMTP_PASSWORD').value;sender=self.secrets.resolve('SMTP_FROM_EMAIL').value
  if not host or not sender:raise DomainValidationError('SMTP_HOST and SMTP_FROM_EMAIL are missing')
  msg=EmailMessage();msg['Subject']=subject;msg['From']=sender;msg['To']=recipient;msg.set_content(text_body);msg.add_alternative(html_body,subtype='html')
  with smtplib.SMTP(host,port,timeout=20) as smtp:
   if self.use_tls:smtp.starttls()
   if user:smtp.login(user,password or '')
   smtp.send_message(msg)
  return {'message_id':idempotency_key}
@dataclass(frozen=True,slots=True)
class DistributionSummary:status:DistributionStatus;queued:int;sent:int;failed:int;suppressed:int;skipped:int
class EmailTemplateRenderer:
 def __init__(self,root=None):self.root=Path(root or Path(__file__).resolve().parents[2]/'templates'/'ad_doctor')
 def render(self,*,name,title,diagnosis,document_url,unsubscribe_url='#unsubscribe'):
  if not re.match(r'^https://',document_url):raise DomainValidationError('A valid HTTPS document URL is required')
  values={'name':name or 'there','title':title,'diagnosis':diagnosis,'document_url':document_url,'unsubscribe_url':unsubscribe_url}
  def one(path,escape):
   text=path.read_text(encoding='utf-8')
   for k,v in values.items():text=text.replace('{{'+k+'}}',html.escape(str(v)) if escape else str(v))
   return text
  return one(self.root/'subscriber_email.html',True),one(self.root/'subscriber_email.txt',False)
class EmailDeliveryService:
 def __init__(self,path:Path,provider,max_attempts=3):self.path=Path(path);self.provider=provider;self.max=max_attempts;self.deliveries=SQLiteDeliveryRepository(path);self.publications=SQLitePublicationRepository(path);self.subscribers=SQLiteSubscriberRepository(path);self.templates=EmailTemplateRenderer();self.lifecycle=CaseLifecycleService(path)
 @staticmethod
 def recipient_hash(email):return hashlib.sha256(email.strip().lower().encode()).hexdigest()
 def _send(self,publication,recipient,subscriber_id,dtype,idempotency_key,name=None,run_id='delivery'):
  existing=self.deliveries.by_key(idempotency_key)
  if existing and existing['status']=='sent':return existing
  if existing and existing['status'] in {'suppressed','cancelled','bounced','complained'}:return existing
  attempt=(existing['attempt_number']+1) if existing else 1
  if attempt>self.max:raise DomainValidationError('Maximum delivery attempts reached')
  html_body,text_body=self.templates.render(name=name,title=publication.document_title,diagnosis='Read the complete Ad Doctor diagnosis.',document_url=publication.google_document_url)
  now=datetime.now(UTC);delivery=Delivery(f'delivery_{uuid4().hex}',publication.publication_id,subscriber_id,dtype,self.recipient_hash(recipient),self.provider.provider_name,None,DeliveryStatus.QUEUED,attempt,idempotency_key,f'The Ad Doctor Diagnoses: {publication.document_title}','subscriber_email_v1',now,originating_run_id=run_id)
  with closing(get_connection(self.path)) as c:
   if existing:c.execute("UPDATE deliveries SET status='sending',attempt_number=?,attempted_at=? WHERE idempotency_key=?",(attempt,now.isoformat(),idempotency_key))
   else:self.deliveries.create_in(c,delivery)
   c.commit()
  try:
   result=self.provider.send(recipient,delivery.subject,html_body,text_body,idempotency_key)
   with closing(get_connection(self.path)) as c:c.execute("UPDATE deliveries SET status='sent',provider_message_id=?,attempted_at=?,delivered_at=?,safe_error_message=NULL WHERE idempotency_key=?",(result['message_id'],now.isoformat(),datetime.now(UTC).isoformat(),idempotency_key));c.commit()
  except Exception as exc:
   retryable=bool(getattr(exc,'retryable',False));safe=type(exc).__name__
   with closing(get_connection(self.path)) as c:c.execute("UPDATE deliveries SET status=?,failed_at=?,error_category=?,safe_error_message=?,retry_after=? WHERE idempotency_key=?",('failed' if retryable else 'suppressed',datetime.now(UTC).isoformat(),'retryable' if retryable else 'non_retryable',safe,(datetime.now(UTC)+timedelta(minutes=2**attempt)).isoformat() if retryable else None,idempotency_key));c.commit()
  return self.deliveries.by_key(idempotency_key)
 def send_test(self,publication_id,recipient,idempotency_key):
  p=self.publications.get(publication_id)
  if not p or p.publishing_status!=PublishingStatus.PUBLISHED:raise DomainValidationError('Test email requires a published Publication')
  return self._send(p,recipient,None,DeliveryType.TEST,idempotency_key)
 def distribute(self,publication_id,strict_consent=True,run_id='distribution'):
  p=self.publications.get(publication_id)
  if not p or p.publishing_status!=PublishingStatus.PUBLISHED:raise DomainValidationError('Current published Publication required')
  if p.visibility!=Visibility.LINK_VIEWER:raise DomainValidationError('Document visibility blocks subscriber delivery')
  eligible=self.subscribers.eligible(strict_consent);sent=failed=suppressed=0
  with closing(get_connection(self.path)) as c:total=int(c.execute('SELECT COUNT(*) FROM subscribers').fetchone()[0])
  ineligible=max(0,total-len(eligible))
  if not eligible:return DistributionSummary(DistributionStatus.FAILED,0,0,0,ineligible,ineligible)
  with closing(get_connection(self.path)) as c:
   row=c.execute('SELECT * FROM cases WHERE case_id=?',(p.case_id,)).fetchone();case=self.lifecycle.cases._from_row(row)
   if case.workflow_status==WorkflowStatus.PUBLISHED.value:case=self.lifecycle._transition(c,case,WorkflowStatus.EMAIL_PENDING,ActorType.SYSTEM,'distribution_service','Subscriber distribution started.',run_id,'email')
   elif case.workflow_status not in {WorkflowStatus.EMAIL_PENDING.value,WorkflowStatus.EMAILED.value}:raise DomainValidationError('Case is not eligible for subscriber distribution')
   c.commit()
  for s in eligible:
   key=hashlib.sha256(f'{publication_id}|{s.subscriber_id}|subscriber'.encode()).hexdigest();r=self._send(p,s.email,s.subscriber_id,DeliveryType.SUBSCRIBER,key,s.name,run_id)
   sent+=r['status']=='sent';failed+=r['status']=='failed';suppressed+=r['status']=='suppressed'
  status=DistributionStatus.COMPLETED if sent and not failed else DistributionStatus.COMPLETED_WITH_FAILURES if sent else DistributionStatus.FAILED
  with closing(get_connection(self.path)) as c:
   c.execute('UPDATE publications SET distribution_status=? WHERE publication_id=?',(status.value,publication_id))
   if sent and case.workflow_status!=WorkflowStatus.EMAILED.value:
    row=c.execute('SELECT * FROM cases WHERE case_id=?',(p.case_id,)).fetchone();case=self.lifecycle.cases._from_row(row);case=self.lifecycle._transition(c,case,WorkflowStatus.EMAILED,ActorType.SYSTEM,'distribution_service','All eligible deliveries terminal; at least one sent.',run_id,'email');case=self.lifecycle._update_case(c,replace(case,publication_status='published'))
   c.commit()
  return DistributionSummary(status,len(eligible),sent,failed,suppressed+ineligible,ineligible)
