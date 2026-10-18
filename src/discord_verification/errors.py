"""Safe, non-sensitive messages that may be shown to members."""


class VerificationError(Exception):
    """Expected rejection, containing no email, name, code, or credentials."""


class DeliveryError(VerificationError):
    """Email could not be submitted; the pending challenge is invalidated."""


class ConfigError(ValueError):
    """Invalid startup configuration, without echoing its value."""
