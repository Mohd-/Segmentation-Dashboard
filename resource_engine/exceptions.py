"""Custom exceptions for the resource assessment engine."""


class ResourceEngineError(Exception):
    """Base class for engine-level errors."""


class ConfigurationError(ResourceEngineError):
    """Raised when scenario configuration is missing or invalid."""


class InputValidationError(ResourceEngineError, ValueError):
    """Raised when user-facing calculation inputs are invalid."""
