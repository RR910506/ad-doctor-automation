from typing import Protocol
from src.domain.publication import FinalDocument,SharingPolicy
class DocumentPublisher(Protocol):
 provider_name:str
 def publish(self,document:FinalDocument,operations:list[dict],folder_path:tuple[str,...],sharing:SharingPolicy,idempotency_key:str)->dict:...
 def archive(self,document_id:str,archive_path:tuple[str,...])->None:...
 def validate_connection(self)->dict:...
class GoogleWorkspaceDocumentPublisher:
 provider_name='google_workspace'
 def __init__(self,docs_client,drive_client):self.docs=docs_client;self.drive=drive_client;self._resources={}
 def publish(self,document,operations,folder_path,sharing,idempotency_key):
  if idempotency_key in self._resources:return self._resources[idempotency_key]
  parent=None
  for name in folder_path:parent=self.drive.ensure_folder(name,parent)
  created=self.docs.create_document(document.title);self.docs.apply_operations(created['id'],operations);self.drive.move_file(created['id'],parent)
  if sharing.visibility.value=='link_viewer':self.drive.set_link_viewer(created['id'])
  elif sharing.visibility.value=='specific_readers':
   for reader in sharing.readers:self.drive.add_reader(created['id'],reader)
  result={'document_id':created['id'],'file_id':created.get('file_id',created['id']),'url':self.docs.view_url(created['id']),'folder_id':parent,'sharing_applied':True};self._resources[idempotency_key]=result;return result
 def archive(self,document_id,archive_path):self.drive.archive(document_id,archive_path)
 def validate_connection(self):return {'configured':True,'provider':self.provider_name}
