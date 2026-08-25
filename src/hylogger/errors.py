class HyLoggerError(Exception):
    """Expected error that should be presented without a traceback in the CLI."""


class ConfigurationError(HyLoggerError):
    """The local project configuration is invalid."""


class RemoteDataError(HyLoggerError):
    """A remote data provider did not return usable data."""


class SelectionError(HyLoggerError):
    """A named selection is missing or invalid."""
