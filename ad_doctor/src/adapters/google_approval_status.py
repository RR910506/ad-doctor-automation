from src.domain.approval import ApprovalStatus
MAP={'pending':ApprovalStatus.PENDING_REVIEW,'approved':ApprovalStatus.APPROVED,'rejected':ApprovalStatus.REJECTED,'revision requested':ApprovalStatus.REVISION_REQUESTED,'revision_requested':ApprovalStatus.REVISION_REQUESTED}
def map_google_approval_status(value:str)->ApprovalStatus:
 key=value.strip().lower()
 if key not in MAP:raise ValueError(f"Unknown Google approval status: {value}")
 return MAP[key]

