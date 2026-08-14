"""Domain-specific errors with stable meanings for services and adapters."""


class DomainValidationError(ValueError):
    """Raised when a domain object violates an invariant."""


class InvalidWorkflowTransition(DomainValidationError):
    """Raised when a workflow state change is not allowed."""


class VersionConflictError(RuntimeError):
    """Raised when an optimistic update uses a stale entity version."""


class EntityNotFoundError(LookupError):
    """Raised when a requested domain entity does not exist."""

