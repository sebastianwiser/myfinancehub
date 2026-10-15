class MainHubError(Exception):
    """Base class for all MainHub errors."""


class MoneyParseError(MainHubError):
    """A string could not be parsed as a USD amount."""


class ConnectorError(MainHubError):
    """A connector could not read or normalize its source data."""


class ReceiptError(MainHubError):
    """A receipt could not be read, validated, or stored."""


class ExtractionError(ReceiptError):
    """The vision model could not produce a usable receipt."""


class ReceiptMismatchError(ReceiptError):
    """Receipt items plus tax do not add up to the total."""
