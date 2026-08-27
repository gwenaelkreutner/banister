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


class SchemaTooNewError(BanisterError):
    """Raised at startup when the database's stamped Alembic revision is not among the
    revisions this running code knows about — spec 003 FR-021. Running anyway would write
    data shaped for a schema structure this version does not understand."""


class MigrationFailedError(BanisterError):
    """Raised at startup when applying a schema migration fails partway — spec 003
    FR-020. The database must be left in its previous working state; this exception
    signals that startup should not proceed as if migration succeeded."""
