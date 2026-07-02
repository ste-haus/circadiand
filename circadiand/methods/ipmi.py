"""IPMI power method (power-on to start; power-off wired but disabled)."""

from typing import Any

from pyghmi.ipmi import command as ipmi_command

from .base import ACTION_DOWN, ACTION_UP, Method, register, require_key
from ..errors import ExecutionError

IPMI_POWER_ON = "on"
IPMI_POWER_OFF = "off"

# Map circadiand's up/down actions onto pyghmi's on/off power states.
_IPMI_STATE = {ACTION_UP: IPMI_POWER_ON, ACTION_DOWN: IPMI_POWER_OFF}


@register
class IpmiMethod(Method):
    """Power a host via its IPMI/BMC using pyghmi.

    Down is implemented but ``SUPPORTS_DOWN`` is False for now (out of scope), so
    the API gates it before it can run.

    Unlike ssh/ping, ipmi does *not* inherit the host-level ``host``: a BMC has
    its own management NIC and IP, distinct from the machine's OS address, so it
    carries its own required ``host``.

    Config:
        host:     BMC address (required — the management IP, not the OS address)
        username: BMC user (required)
        password: BMC password (required, never logged)
    """

    TYPE = "ipmi"
    SUPPORTS_UP = True
    SUPPORTS_DOWN = False

    def __init__(self, hostname: str, **config: Any):
        super().__init__(hostname, **config)
        self.host = require_key(config, "host", hostname, self.TYPE)
        self.username = require_key(config, "username", hostname, self.TYPE)
        self.password = require_key(config, "password", hostname, self.TYPE)

    def _set_power(self, action: str) -> str:
        state = _IPMI_STATE[action]
        try:
            conn = ipmi_command.Command(
                bmc=self.host, userid=self.username, password=self.password
            )
            try:
                conn.set_power(state, wait=True)
            finally:
                session = getattr(conn, "ipmi_session", None)
                if session is not None:
                    try:
                        session.logout()
                    except Exception:
                        pass
        except Exception as exc:  # pyghmi raises a variety of error types
            raise ExecutionError(self.TYPE, action, str(exc)) from exc
        return f"IPMI set power {state} on {self.host}"

    def power_up(self) -> str:
        return self._set_power(ACTION_UP)

    def power_down(self) -> str:
        return self._set_power(ACTION_DOWN)
