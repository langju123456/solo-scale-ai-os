"""Project-specific exceptions for BuildLog."""


class BuildLogError(Exception):
    """Base exception for BuildLog failures."""


class InputFileError(BuildLogError):
    """Raised when an input file cannot be read or parsed."""


class ModelResponseError(BuildLogError):
    """Raised when a model call fails."""


class StructuredOutputError(BuildLogError):
    """Raised when structured model output cannot be validated."""


class TraceWriteError(BuildLogError):
    """Raised when trace artifacts cannot be written."""


class PersistenceError(BuildLogError):
    """Raised when structured metadata cannot be persisted."""


class PromptFileError(BuildLogError):
    """Raised when a versioned prompt file cannot be loaded."""


class PackageBuildError(BuildLogError):
    """Raised when a publishing package cannot be planned or rendered."""
