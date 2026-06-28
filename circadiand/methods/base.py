"""Generic power-method construct.

A *method* is one way to power a host on or off (WOL, IPMI, SSH, ...). Adding a
new method is a single file: subclass :class:`Method`, set ``TYPE`` and the
``SUPPORTS_*`` flags, implement the action(s) it supports, and decorate with
:func:`register`. ``methods/__init__`` imports every submodule so registration
happens at import time, making :data:`METHOD_REGISTRY` the single source of
truth for the valid ``type`` values accepted in config.
"""

from abc import ABC
from typing import Any

from ..errors import ConfigError, UnsupportedAction

ACTION_UP = "up"
ACTION_DOWN = "down"
ACTIONS = (ACTION_UP, ACTION_DOWN)

# Populated by @register at import time: method type string -> Method subclass.
METHOD_REGISTRY: dict[str, type["Method"]] = {}


def register(cls: type["Method"]) -> type["Method"]:
    """Class decorator that records a Method subclass under its ``TYPE``."""
    if not getattr(cls, "TYPE", None):
        raise ValueError(f"{cls.__name__} must define a non-empty TYPE")
    METHOD_REGISTRY[cls.TYPE] = cls
    return cls


def require_key(config: dict[str, Any], key: str, hostname: str, method_type: str) -> Any:
    """Fetch a required config key, raising a startup ConfigError if absent."""
    if key not in config or config[key] in (None, ""):
        raise ConfigError(
            f"host '{hostname}' method '{method_type}' is missing required key '{key}'"
        )
    return config[key]


class Method(ABC):
    """Base class for all power methods.

    Subclasses validate their own config in ``__init__`` (raising
    :class:`ConfigError` with host/type context) so a malformed config fails at
    startup rather than on the first request.
    """

    TYPE: str = ""
    SUPPORTS_UP: bool = False
    SUPPORTS_DOWN: bool = False

    def __init__(self, hostname: str, **config: Any):
        self.hostname = hostname

    def supports(self, action: str) -> bool:
        return {ACTION_UP: self.SUPPORTS_UP, ACTION_DOWN: self.SUPPORTS_DOWN}[action]

    def run(self, action: str) -> str:
        """Dispatch to power_up/power_down by action name."""
        return {ACTION_UP: self.power_up, ACTION_DOWN: self.power_down}[action]()

    def power_up(self) -> str:
        raise UnsupportedAction(self.TYPE, ACTION_UP)

    def power_down(self) -> str:
        raise UnsupportedAction(self.TYPE, ACTION_DOWN)
