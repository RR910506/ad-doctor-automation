import json
from contextlib import closing
from pathlib import Path
from src.db import get_connection
from src.domain.content_package import ContentPackage
class SQLiteContentPackageRepository:
 def __init__(self,path:Path):self.path=Path(path)
 def create(self,p):
  with closing(get_connection(self.path)) as c:self.create_in(c,p);c.commit()
  return p
 def create_in(self,c,p):c.execute('INSERT INTO content_packages(content_package_id,case_id,evidence_pack_id,draft_id,schema_version,package_version,parent_package_version,revision_reason,content_type,generation_provider,generation_model,generated_at,payload_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(p.content_package_id,p.case_id,p.evidence_pack_id,p.draft_id,p.schema_version,p.package_version,p.parent_package_version,p.revision_reason,p.content_type,p.generation_provider,p.generation_model,p.generated_at.isoformat(),json.dumps(p.to_dict()),p.created_at.isoformat(),p.updated_at.isoformat()))
 def latest(self,case_id):return self._one('case_id=? ORDER BY package_version DESC LIMIT 1',(case_id,))
 def get(self,package_id):return self._one('content_package_id=?',(package_id,))
 def history(self,case_id):
  with closing(get_connection(self.path)) as c:rows=c.execute('SELECT payload_json FROM content_packages WHERE case_id=? ORDER BY package_version',(case_id,)).fetchall()
  return [ContentPackage.from_dict(json.loads(r[0])) for r in rows]
 def _one(self,q,p):
  with closing(get_connection(self.path)) as c:r=c.execute(f'SELECT payload_json FROM content_packages WHERE {q}',p).fetchone()
  return ContentPackage.from_dict(json.loads(r[0])) if r else None
