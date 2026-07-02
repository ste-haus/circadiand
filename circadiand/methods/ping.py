"""ICMP ping liveliness method.

A ``ping`` method only probes reachability — it can neither power a host up nor
down (``SUPPORTS_CHECK`` only). It is meant to be named by a host's ``health``
config and polled by the background :class:`~circadiand.health.HealthMonitor`.
"""

from typing import Any

import icmplib

from .base import ACTION_CHECK, Method, register, require_key
from ..errors import ExecutionError

DEFAULT_PING_COUNT = 1
DEFAULT_PING_TIMEOUT_SECONDS = 2
# icmplib uses raw sockets when privileged; the container runs as root so this
# works out of the box. Set to False to use unprivileged datagram sockets where
# net.ipv4.ping_group_range permits.
DEFAULT_PING_PRIVILEGED = True


@register
class PingMethod(Method):
    """Probe a host's liveliness with ICMP echo requests via ``icmplib``.

    Config:
        host:       target address. Normally inherited from the host-level
                    ``host`` field; only set here to override it.
        count:      echo requests to send (optional, default 1)
        timeout:    seconds to wait per request (optional, default 2)
        privileged: use raw sockets (optional, default True)
    """

    TYPE = "ping"
    SUPPORTS_CHECK = True
    USES_SHARED_HOST = True

    def __init__(self, hostname: str, **config: Any):
        super().__init__(hostname, **config)
        self.host = require_key(config, "host", hostname, self.TYPE)
        self.count = int(config.get("count", DEFAULT_PING_COUNT))
        self.timeout = float(config.get("timeout", DEFAULT_PING_TIMEOUT_SECONDS))
        self.privileged = bool(config.get("privileged", DEFAULT_PING_PRIVILEGED))

    def check(self) -> bool:
        try:
            result = icmplib.ping(
                self.host,
                count=self.count,
                timeout=self.timeout,
                privileged=self.privileged,
            )
        except Exception as exc:  # icmplib raises a variety of error types
            raise ExecutionError(self.TYPE, ACTION_CHECK, str(exc)) from exc
        return result.is_alive
