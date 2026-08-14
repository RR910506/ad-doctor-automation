from __future__ import annotations
import json
from contextlib import closing
from datetime import datetime
from pathlib import Path
from src.db import get_connection
from src.domain.approval import *
class SQLiteApprovalRepository:
 def __init__(self,path:Path):self.path=Path(path)
 def append(self,e:ApprovalEvent)->ApprovalEvent:
  with closing(get_connection(self.path)) as c:self.append_in(c,e);c.commit()
  return e
 def append_in(self,c,e): c.execute("INSERT OR IGNORE INTO approval_events(approval_event_id,idempotency_key,case_id,draft_id,previous_status,new_status,decision,reviewer_id,actor_type,reviewer_notes,requested_corrections,created_at,case_version,originating_run_id,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(e.approval_event_id,e.idempotency_key,e.case_id,e.draft_id,e.previous_status.value,e.new_status.value,e.decision.value,e.reviewer_id,e.actor_type.value,e.reviewer_notes,e.requested_corrections,e.created_at.isoformat(),e.case_version,e.originating_run_id,json.dumps(e.metadata)))
 def history(self,case_id):
  with closing(get_connection(self.path)) as c: rows=c.execute("SELECT * FROM approval_events WHERE case_id=? ORDER BY created_at,id",(case_id,)).fetchall()
  return [self._row(r) for r in rows]
 def latest(self,case_id,draft_id=None):
  q="SELECT * FROM approval_events WHERE case_id=?";p=[case_id]
  if draft_id is not None:q+=" AND draft_id=?";p.append(str(draft_id))
  q+=" ORDER BY created_at DESC,id DESC LIMIT 1"
  with closing(get_connection(self.path)) as c:r=c.execute(q,p).fetchone()
  return self._row(r) if r else None
 def _row(self,r): return ApprovalEvent(r['approval_event_id'],r['idempotency_key'],r['case_id'],r['draft_id'],ApprovalStatus(r['previous_status']),ApprovalStatus(r['new_status']),ApprovalDecision(r['decision']),r['reviewer_id'],ApprovalActorType(r['actor_type']),r['reviewer_notes'],r['requested_corrections'],datetime.fromisoformat(r['created_at']),r['case_version'],r['originating_run_id'],json.loads(r['metadata_json']))
