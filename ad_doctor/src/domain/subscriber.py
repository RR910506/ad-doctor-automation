from dataclasses import asdict,dataclass,field,replace
from datetime import datetime
from enum import StrEnum
from typing import Any
import re
from src.domain.errors import DomainValidationError
class SubscriberStatus(StrEnum):ACTIVE='active';PAUSED='paused';UNSUBSCRIBED='unsubscribed';BOUNCED='bounced';COMPLAINED='complained';SUPPRESSED='suppressed'
class ConsentStatus(StrEnum):CONFIRMED='confirmed';PENDING='pending';WITHDRAWN='withdrawn';LEGACY_UNKNOWN='legacy_unknown'
@dataclass(frozen=True,slots=True)
class Subscriber:
 subscriber_id:str;email:str;normalized_email:str;name:str|None;status:SubscriberStatus;consent_status:ConsentStatus;consent_source:str|None;consent_timestamp:datetime|None;content_preferences:tuple[str,...];frequency:str;segment:str|None;locale:str;verification_status:str;last_sent_at:datetime|None;unsubscribe_token_hash:str|None;suppression_reason:str|None;created_at:datetime;updated_at:datetime;metadata:dict[str,Any]=field(default_factory=dict);version:int=1
 def __post_init__(self):
  normalized=self.email.strip().lower()
  if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$',normalized):raise DomainValidationError('Invalid subscriber email')
  if self.normalized_email!=normalized:raise DomainValidationError('normalized_email must be lowercase canonical email')
  if self.version<1:raise DomainValidationError('Subscriber version must be positive')
 def eligible(self,strict_consent=True):return self.status==SubscriberStatus.ACTIVE and (self.consent_status==ConsentStatus.CONFIRMED or (not strict_consent and self.consent_status==ConsentStatus.LEGACY_UNKNOWN))
 def masked_email(self):
  left,domain=self.normalized_email.split('@',1);return left[:1]+'***@'+domain
