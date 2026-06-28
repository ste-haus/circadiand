"""Domain exceptions for circadiand.

Two families:

* ``ConfigError`` is raised while loading/validating the YAML config — it is a
  startup failure (fail fast, never reaches a request).
* The request-time errors (``HostNotFound`` etc.) carry the HTTP status the API
  layer should translate them to via the ``status_code`` attribute.
"""

from http import HTTPStatus


class CircadiandError(Exception):
    """Base class for all circadiand errors."""


class ConfigError(CircadiandError):
    """Raised when the configuration file is invalid. Fails startup."""


class RequestError(CircadiandError):
    """Base for errors that map to an HTTP response."""

    status_code = HTTPStatus.INTERNAL_SERVER_ERROR


class HostNotFound(RequestError):
    status_code = HTTPStatus.NOT_FOUND

    def __init__(self, hostname: str):
        self.hostname = hostname
        super().__init__(f"unknown host '{hostname}'")


class MethodNotFound(RequestError):
    status_code = HTTPStatus.NOT_FOUND

    def __init__(self, hostname: str, method: str):
        self.hostname = hostname
        self.method = method
        super().__init__(f"host '{hostname}' has no method '{method}'")


class NoDefaultMethod(RequestError):
    status_code = HTTPStatus.BAD_REQUEST

    def __init__(self, hostname: str, action: str):
        self.hostname = hostname
        self.action = action
        super().__init__(
            f"no method specified and no default configured for action "
            f"'{action}' on host '{hostname}'"
        )


class UnsupportedAction(RequestError):
    status_code = HTTPStatus.BAD_REQUEST

    def __init__(self, method_type: str, action: str):
        self.method_type = method_type
        self.action = action
        super().__init__(f"method '{method_type}' does not support action '{action}'")


class ExecutionError(RequestError):
    """A power method failed while talking to the target (driver-level failure)."""

    status_code = HTTPStatus.BAD_GATEWAY

    def __init__(self, method_type: str, action: str, detail: str):
        self.method_type = method_type
        self.action = action
        self.detail = detail
        super().__init__(f"method '{method_type}' failed action '{action}': {detail}")
