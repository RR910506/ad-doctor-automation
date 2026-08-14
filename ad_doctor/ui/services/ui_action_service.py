class UIActionService:
 """Deliberately read-only in Phase 4A; future actions delegate to backend services here."""
 def unavailable(self,action):return {'allowed':False,'message':f'{action} is read-only in Phase 4A.'}
