"""Exception hierarchy.

Monitoring code never lets these reach the user's scraper (fail open); the publish gate and
the CLI raise them on purpose (fail closed).
"""


class ScrapewatchError(Exception):
    """Base class for every error raised by scrapewatch."""


class ConfigError(ScrapewatchError):
    """A rules file, cost model or config value is invalid."""


class RulesError(ConfigError):
    """A guard-rules or data-rules file failed to load or validate."""


class HistoryError(ScrapewatchError):
    """The history store could not be opened, written or read."""


class GateError(ScrapewatchError):
    """The publish gate refused to promote (held, locked or inconsistent)."""


class CheckError(ScrapewatchError):
    """The checks engine could not run; the verdict is hold (fail closed)."""


class AlertError(ScrapewatchError):
    """An alert could not be delivered. Counted, never fatal."""
