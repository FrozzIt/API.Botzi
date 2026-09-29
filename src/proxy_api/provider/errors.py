class ProviderError(RuntimeError):
    """Base error that contains no raw provider response or secret."""


class ProviderUnavailable(ProviderError):
    """The provider or required shared infrastructure is unavailable."""


class ProviderDeadlineExceeded(ProviderError):
    """The bounded provider operation could not finish in time."""


class ProviderAuthenticationError(ProviderError):
    """Server-side provider authentication failed."""


class ProviderProtocolError(ProviderError):
    """The provider returned an unsupported response."""
