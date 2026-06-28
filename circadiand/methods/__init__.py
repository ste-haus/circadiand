"""Power methods package.

Importing this package self-registers every built-in method into
:data:`METHOD_REGISTRY`. New methods are added by creating a module here and
importing it below.
"""

from .base import (
    ACTION_DOWN,
    ACTION_UP,
    ACTIONS,
    METHOD_REGISTRY,
    Method,
    register,
    require_key,
)

# Import side effects register each method via the @register decorator.
from . import ipmi, ssh, wol  # noqa: E402,F401

__all__ = [
    "ACTION_DOWN",
    "ACTION_UP",
    "ACTIONS",
    "METHOD_REGISTRY",
    "Method",
    "register",
    "require_key",
]
