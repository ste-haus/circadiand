"""SSH power-off method.

Connects as the dedicated ``circadiand`` identity (private key injected into the
container) and runs a shutdown command on the target. Host-key checking uses
AutoAddPolicy — pragmatic for a trusted network; tighten to a mounted
known_hosts later if desired.
"""

import os
from typing import Any

import paramiko

from .base import ACTION_DOWN, Method, register, require_key
from ..errors import ConfigError, ExecutionError

DEFAULT_SSH_PORT = 22
DEFAULT_SSH_USERNAME = "circadiand"
DEFAULT_SHUTDOWN_COMMAND = "sudo shutdown -h now"
ENV_SSH_KEY = "CIRCADIAND_SSH_KEY"
CONNECT_TIMEOUT_SECONDS = 10
COMMAND_TIMEOUT_SECONDS = 15


@register
class SshMethod(Method):
    """Power a host off by running a shutdown command over SSH.

    Config:
        host:             target address (required)
        username:         SSH user (optional, default "circadiand")
        port:             SSH port (optional, default 22)
        key_path:         private key path (optional, defaults to
                          $CIRCADIAND_SSH_KEY)
        shutdown_command: command to run (optional, default
                          "sudo shutdown -h now")
    """

    TYPE = "ssh"
    SUPPORTS_DOWN = True

    def __init__(self, hostname: str, **config: Any):
        super().__init__(hostname, **config)
        self.host = require_key(config, "host", hostname, self.TYPE)
        self.username = config.get("username", DEFAULT_SSH_USERNAME)
        self.port = int(config.get("port", DEFAULT_SSH_PORT))
        self.key_path = config.get("key_path") or os.getenv(ENV_SSH_KEY)
        if not self.key_path:
            raise ConfigError(
                f"host '{hostname}' method '{self.TYPE}' has no key_path and "
                f"${ENV_SSH_KEY} is unset"
            )
        self.shutdown_command = config.get("shutdown_command", DEFAULT_SHUTDOWN_COMMAND)

    def power_down(self) -> str:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                hostname=self.host,
                port=self.port,
                username=self.username,
                key_filename=self.key_path,
                timeout=CONNECT_TIMEOUT_SECONDS,
            )
            _, stdout, stderr = client.exec_command(
                self.shutdown_command, timeout=COMMAND_TIMEOUT_SECONDS
            )
            exit_status = stdout.channel.recv_exit_status()
            if exit_status != 0:
                detail = stderr.read().decode(errors="replace").strip() or "no stderr"
                raise ExecutionError(
                    self.TYPE, ACTION_DOWN, f"exit {exit_status}: {detail}"
                )
        except ExecutionError:
            raise
        except Exception as exc:
            raise ExecutionError(self.TYPE, ACTION_DOWN, str(exc)) from exc
        finally:
            client.close()
        return f"ran '{self.shutdown_command}' on {self.host}"
