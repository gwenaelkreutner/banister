"""Error types for the intervals.icu client (spec 002 FR-005, FR-013).

Distinct types rather than one generic exception: a caller must be able to tell "your
credential was revoked" (tell the athlete, stop retrying) from "briefly unavailable"
(retry quietly) from "rate limited" (back off) — collapsing these makes FR-005 and FR-013
unimplementable above the client boundary (contracts/sport-provider.md).
"""
from __future__ import annotations


class IntervalsError(Exception):
    """Base class for all intervals.icu client errors."""


class CredentialRejectedError(IntervalsError):
    """The API key is missing, malformed, or no longer accepted (FR-003, FR-005).

    Raised at startup verification, or during operation when a previously working
    credential stops being accepted. The caller must inform the athlete rather than
    retry — retrying a rejected credential wastes quota and never succeeds.
    """


class RateLimitedError(IntervalsError):
    """The source's request quota was exceeded (FR-013).

    The caller must back off rather than retry immediately — retrying at the same pace
    would keep failing and risks exhausting the quota further.
    """

    def __init__(self, message: str, *, retry_after_seconds: float | None = None):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class TransientError(IntervalsError):
    """A temporary failure — network issue, timeout, or a 5xx response (FR-013).

    The caller should retry with backoff. Distinct from RateLimitedError because the
    correct response differs: a transient failure can be retried soon, a rate limit
    cannot.
    """
