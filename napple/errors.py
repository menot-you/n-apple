"""Typed failures. Each carries the App Store Connect status, code and detail."""

from __future__ import annotations


class AppleError(Exception):
    """A request to Apple did not give the expected result."""

    def __init__(
        self, message: str, *, status: int | None = None, code: str | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


class ConfigError(AppleError):
    """The API key configuration is missing or not usable."""


class AuthError(AppleError):
    """Apple refused the token (HTTP 401, or 403 without an agreement code)."""


class AgreementError(AppleError):
    """A required agreement is missing or expired (403 FORBIDDEN.REQUIRED_AGREEMENTS_MISSING_OR_EXPIRED)."""


class NotFoundError(AppleError):
    """The resource does not exist (HTTP 404)."""


class ConflictError(AppleError):
    """Apple refused the write because of the current state (HTTP 409)."""


class RateLimitError(AppleError):
    """HTTP 429 continued after the bounded retries."""


class ProtocolError(AppleError):
    """A response, a redirect or a pagination link is outside the API contract."""


class UploadError(AppleError):
    """A build upload did not reach a terminal success state."""
