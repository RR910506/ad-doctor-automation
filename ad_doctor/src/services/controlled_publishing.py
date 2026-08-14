from src.case_files import get_case_files_mode
class ControlledPublishingFacade:
 """Feature gate for future UI/workers; active publishing remains reserved."""
 def __init__(self,service):self.service=service
 def publish(self,*args,**kwargs):
  mode=get_case_files_mode()
  if mode=='off':return {'executed':False,'mode':'off','reason':'legacy_authoritative'}
  if mode in {'shadow','active'} and not getattr(self.service.provider,'is_fake',False):return {'executed':False,'mode':mode,'reason':'real_side_effects_disabled'}
  return {'executed':True,'mode':mode,'publication':self.service.publish(*args,**kwargs)}
