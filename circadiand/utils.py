"""Small environment-variable helpers (env overrides config/defaults)."""

import os
from typing import Optional


def get_env_str(key: str, default: Optional[str] = None) -> Optional[str]:
    value = os.getenv(key)
    return value if value not in (None, "") else default


def get_env_int(key: str, default: int) -> int:
    try:
        return int(os.environ[key])
    except (KeyError, ValueError):
        return default
