from dataclasses import asdict,dataclass,field
from datetime import datetime
from enum import StrEnum
from typing import Any
class DeliveryType(StrEnum):TEST='test';SUBSCRIBER='subscriber';ADMIN_ALERT='admin_alert'
class DeliveryStatus(StrEnum):QUEUED='queued';SENDING='sending';SENT='sent';DELIVERED='delivered';FAILED='failed';BOUNCED='bounced';COMPLAINED='complained';SUPPRESSED='suppressed';CANCELLED='cancelled'
class DistributionStatus(StrEnum):NOT_STARTED='not_started';QUEUED='queued';IN_PROGRESS='in_progress';COMPLETED='completed';COMPLETED_WITH_FAILURES='completed_with_failures';FAILED='failed';CANCELLED='cancelled'
@dataclass(frozen=True,slots=True)
class Delivery:
 delivery_id:str;publication_id:str;subscriber_id:str|None;delivery_type:DeliveryType;recipient_hash:str;provider:str;provider_message_id:str|None;status:DeliveryStatus;attempt_number:int;idempotency_key:str;subject:str;template_version:str;queued_at:datetime;attempted_at:datetime|None=None;delivered_at:datetime|None=None;failed_at:datetime|None=None;error_category:str|None=None;safe_error_message:str|None=None;retry_after:datetime|None=None;originating_run_id:str|None=None;metadata:dict[str,Any]=field(default_factory=dict)
