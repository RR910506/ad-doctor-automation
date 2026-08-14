import json
from contextlib import closing
from pathlib import Path
from src.db import get_connection
from src.domain.quality_review import QualityReview
class SQLiteQualityReviewRepository:
 def __init__(self,path:Path):self.path=Path(path)
 def create(self,r):
  with closing(get_connection(self.path)) as c:self.create_in(c,r);c.commit()
  return r
 def create_in(self,c,r):c.execute('INSERT INTO quality_reviews(quality_review_id,case_id,content_package_id,package_version,review_version,gate_status,overall_score,auditor_provider,auditor_model,reviewed_at,payload_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(r.quality_review_id,r.case_id,r.content_package_id,r.package_version,r.review_version,r.gate_status.value,r.overall_score,r.auditor_provider,r.auditor_model,r.reviewed_at.isoformat(),json.dumps(r.to_dict())))
 def history(self,pid):
  with closing(get_connection(self.path)) as c:rows=c.execute('SELECT payload_json FROM quality_reviews WHERE content_package_id=? ORDER BY review_version',(pid,)).fetchall()
  return [QualityReview.from_dict(json.loads(r[0])) for r in rows]
