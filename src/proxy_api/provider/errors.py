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


class ProviderObjectNotFound(ProviderError):
    """The provider neutrally reported that a read target is unavailable."""
