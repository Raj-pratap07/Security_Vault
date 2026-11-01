class FireflyError(Exception):
    """Base Firefly application exception."""


class ConfigurationError(FireflyError):
    """Configuration or environment problem."""


class AuthenticationError(FireflyError):
    """Authentication or authorization problem."""


class InvalidSecretError(FireflyError):
    """Secret validation failed."""


class RecordNotFoundError(FireflyError):
    """Requested record does not exist."""


class RecoveryError(FireflyError):
    """Unable to recover a valid secret."""


class ProviderError(FireflyError):
    """Provider storage error."""
