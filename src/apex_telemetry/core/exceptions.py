from typing import Any


class ApexException(Exception):
    """Base exception for all ApexTelemetry domain errors."""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class EntityNotFoundError(ApexException):
    """Raised when a requested resource (e.g. Session, Job) is not found."""


class IdempotencyConflictError(ApexException):
    """Raised when an Idempotency-Key is reused with a different payload hash."""


class BatchValidationError(ApexException):
    """Raised when records within a batch fail domain constraints or physical limits."""


class ConcurrencyConflictError(ApexException):
    """Raised when concurrent operations attempt conflicting writes on identical keys."""
