class MainHubError(Exception):
    """Base class for all MainHub errors."""


class MoneyParseError(MainHubError):
    """A string could not be parsed as a USD amount."""


class ConnectorError(MainHubError):
    """A connector could not read or normalize its source data."""
