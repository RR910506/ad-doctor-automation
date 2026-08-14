from pathlib import Path
class PromptLoader:
 def __init__(self,root:Path|None=None):self.root=root or Path(__file__).resolve().parents[2]/'templates'/'ad_doctor'
 def load(self,name):
  path=self.root/f'{name}.md'
  if not path.is_file():raise FileNotFoundError(f'Ad Doctor prompt is missing: {path}')
  return path.read_text(encoding='utf-8')
 def version(self,name):
  import hashlib
  return hashlib.sha256(self.load(name).encode()).hexdigest()[:12]
