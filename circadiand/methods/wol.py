"""Wake-on-LAN power-on method."""

from typing import Any

import wakeonlan

from .base import Method, register, require_key

DEFAULT_WOL_PORT = 9
DEFAULT_WOL_COUNT = 10


@register
class WolMethod(Method):
    """Power a host on by sending a Wake-on-LAN magic packet.

    Config:
        mac:       target NIC MAC address (required)
        broadcast: broadcast/IP address to send to (optional, defaults to the
                   global broadcast handled by the wakeonlan library)
        port:      UDP port for the magic packet (optional, default 9)
        count:     number of magic packets to send (optional, default 10).
                   Magic packets are fire-and-forget UDP; sending several
                   guards against a single packet being dropped.
    """

    TYPE = "wol"
    SUPPORTS_UP = True

    def __init__(self, hostname: str, **config: Any):
        super().__init__(hostname, **config)
        self.mac = require_key(config, "mac", hostname, self.TYPE)
        self.broadcast = config.get("broadcast")
        self.port = int(config.get("port", DEFAULT_WOL_PORT))
        self.count = int(config.get("count", DEFAULT_WOL_COUNT))

    def power_up(self) -> str:
        kwargs: dict[str, Any] = {"port": self.port}
        if self.broadcast:
            kwargs["ip_address"] = self.broadcast
        for _ in range(self.count):
            wakeonlan.send_magic_packet(self.mac, **kwargs)
        return f"sent {self.count} Wake-on-LAN magic packet(s) to {self.mac}"
