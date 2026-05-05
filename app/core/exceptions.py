class BanisterError(Exception):
    """Base exception for all Banister application errors."""


class ProviderError(BanisterError):
    """Raised when a sport provider operation fails."""


class ProviderAuthError(ProviderError):
    """Raised when provider authentication fails (bad token, expired key)."""


class ProviderRateLimitError(ProviderError):
    """Raised when provider API rate limit is exceeded."""


class PlanNotFoundError(BanisterError):
    """Raised when no active training plan exists for a user."""


class ProfileNotFoundError(BanisterError):
    """Raised when no athlete profile exists for a user."""


class PersonaNotFoundError(BanisterError):
    """Raised when a persona YAML file cannot be found."""
