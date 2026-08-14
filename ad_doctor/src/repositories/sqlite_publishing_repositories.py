from __future__ import annotations
import json
from contextlib import closing
from datetime import datetime
from pathlib import Path
from src.db import get_connection
from src.domain.publication import *
from src.domain.subscriber import *
from src.domain.delivery import *
class SQLitePublicationRepository:
 def __init__(self,path):self.path=Path(path)
 def create_in(self,c,p):c.execute('INSERT INTO publications(publication_id,case_id,content_package_id,package_version,approval_event_id,quality_review_id,publication_version,publishing_status,document_provider,google_document_id,google_document_url,google_drive_folder_id,google_drive_file_id,document_title,document_checksum,visibility,published_at,published_by,originating_run_id,created_at,updated_at,supersedes_publication_id,metadata_json,idempotency_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(p.publication_id,p.case_id,p.content_package_id,p.package_version,p.approval_event_id,p.quality_review_id,p.publication_version,p.publishing_status.value,p.document_provider,p.google_document_id,p.google_document_url,p.google_drive_folder_id,p.google_drive_file_id,p.document_title,p.document_checksum,p.visibility.value,p.published_at.isoformat() if p.published_at else None,p.published_by,p.originating_run_id,p.created_at.isoformat(),p.updated_at.isoformat(),p.supersedes_publication_id,json.dumps(p.metadata),p.idempotency_key))
 def current(self,case_id):return self._one("case_id=? AND publishing_status='published'",(case_id,))
 def by_key(self,key):return self._one('idempotency_key=?',(key,))
 def get(self,pid):return self._one('publication_id=?',(pid,))
 def history(self,case_id):
  with closing(get_connection(self.path)) as c:rows=c.execute('SELECT * FROM publications WHERE case_id=? ORDER BY publication_version',(case_id,)).fetchall()
  return [self._row(r) for r in rows]
 def _one(self,q,p):
  with closing(get_connection(self.path)) as c:r=c.execute(f'SELECT * FROM publications WHERE {q}',p).fetchone()
  return self._row(r) if r else None
 def _row(self,r):return Publication(r['publication_id'],r['case_id'],r['content_package_id'],r['package_version'],r['approval_event_id'],r['quality_review_id'],r['publication_version'],PublishingStatus(r['publishing_status']),r['document_provider'],r['google_document_id'],r['google_document_url'],r['google_drive_folder_id'],r['google_drive_file_id'],r['document_title'],r['document_checksum'],Visibility(r['visibility']),datetime.fromisoformat(r['published_at']) if r['published_at'] else None,r['published_by'],r['originating_run_id'],datetime.fromisoformat(r['created_at']),datetime.fromisoformat(r['updated_at']),r['supersedes_publication_id'],json.loads(r['metadata_json']),r['idempotency_key'])
class SQLiteSubscriberRepository:
 def __init__(self,path):self.path=Path(path)
 def create(self,s):
  with closing(get_connection(self.path)) as c:self.save_in(c,s);c.commit()
  return s
 def save_in(self,c,s):c.execute('INSERT INTO subscribers(subscriber_id,email,normalized_email,name,status,consent_status,consent_source,consent_timestamp,content_preferences_json,frequency,segment,locale,verification_status,last_sent_at,unsubscribe_token_hash,suppression_reason,created_at,updated_at,metadata_json,version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(s.subscriber_id,s.email,s.normalized_email,s.name,s.status.value,s.consent_status.value,s.consent_source,s.consent_timestamp.isoformat() if s.consent_timestamp else None,json.dumps(s.content_preferences),s.frequency,s.segment,s.locale,s.verification_status,s.last_sent_at.isoformat() if s.last_sent_at else None,s.unsubscribe_token_hash,s.suppression_reason,s.created_at.isoformat(),s.updated_at.isoformat(),json.dumps(s.metadata),s.version))
 def get(self,sid):return self._one('subscriber_id=?',(sid,))
 def by_email(self,email):return self._one('normalized_email=?',(email.strip().lower(),))
 def eligible(self,strict=True):
  with closing(get_connection(self.path)) as c:rows=c.execute("SELECT * FROM subscribers WHERE status='active'").fetchall()
  return [s for s in map(self._row,rows) if s.eligible(strict)]
 def _one(self,q,p):
  with closing(get_connection(self.path)) as c:r=c.execute(f'SELECT * FROM subscribers WHERE {q}',p).fetchone()
  return self._row(r) if r else None
 def _row(self,r):return Subscriber(r['subscriber_id'],r['email'],r['normalized_email'],r['name'],SubscriberStatus(r['status']),ConsentStatus(r['consent_status']),r['consent_source'],datetime.fromisoformat(r['consent_timestamp']) if r['consent_timestamp'] else None,tuple(json.loads(r['content_preferences_json'])),r['frequency'],r['segment'],r['locale'],r['verification_status'],datetime.fromisoformat(r['last_sent_at']) if r['last_sent_at'] else None,r['unsubscribe_token_hash'],r['suppression_reason'],datetime.fromisoformat(r['created_at']),datetime.fromisoformat(r['updated_at']),json.loads(r['metadata_json']),r['version'])
class SQLiteDeliveryRepository:
 def __init__(self,path):self.path=Path(path)
 def create_in(self,c,d):c.execute('INSERT OR IGNORE INTO deliveries(delivery_id,publication_id,subscriber_id,delivery_type,recipient_hash,provider,provider_message_id,status,attempt_number,idempotency_key,subject,template_version,queued_at,attempted_at,delivered_at,failed_at,error_category,safe_error_message,retry_after,originating_run_id,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(d.delivery_id,d.publication_id,d.subscriber_id,d.delivery_type.value,d.recipient_hash,d.provider,d.provider_message_id,d.status.value,d.attempt_number,d.idempotency_key,d.subject,d.template_version,d.queued_at.isoformat(),d.attempted_at.isoformat() if d.attempted_at else None,d.delivered_at.isoformat() if d.delivered_at else None,d.failed_at.isoformat() if d.failed_at else None,d.error_category,d.safe_error_message,d.retry_after.isoformat() if d.retry_after else None,d.originating_run_id,json.dumps(d.metadata)))
 def by_key(self,key):
  with closing(get_connection(self.path)) as c:r=c.execute('SELECT * FROM deliveries WHERE idempotency_key=?',(key,)).fetchone()
  return dict(r) if r else None
