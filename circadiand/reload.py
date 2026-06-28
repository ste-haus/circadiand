"""Live config reloading.

``ConfigStore`` holds the currently-active :class:`~circadiand.config.Config`
behind a single attribute; API routes read through it so a reload is a single
atomic reference swap (safe under the GIL — no lock needed). A background daemon
thread polls the config file's mtime and reloads on change.

mtime polling is used deliberately instead of inotify/watchdog: mounted config
files (Docker bind mounts, k8s ConfigMap symlink swaps) frequently don't deliver
reliable inotify events, whereas a periodic ``stat()`` always notices. A bad
edit logs an error and leaves the previous config in place — a malformed file
never takes the service down.
"""

import logging
import threading
import time
from pathlib import Path

from .config import Config, load_config
from .errors import ConfigError

DEFAULT_RELOAD_INTERVAL_SECONDS = 120

_LOGGER = logging.getLogger("circadiand")


class ConfigStore:
    """Holds the active Config and reloads it from disk on demand."""

    def __init__(self, path: str | Path, config: Config):
        self.path = Path(path)
        self._config = config

    @property
    def config(self) -> Config:
        return self._config

    def reload(self) -> bool:
        """Re-parse the config file. On success swap it in and return True; on a
        validation error keep the current config, log, and return False."""
        try:
            new_config = load_config(self.path)
        except ConfigError as exc:
            _LOGGER.error("config reload failed, keeping previous config: %s", exc)
            return False
        self._config = new_config
        _LOGGER.info(
            "config reloaded: %d host(s): %s",
            len(new_config.hosts),
            ", ".join(new_config.hosts),
        )
        return True


def start_config_watcher(store: ConfigStore, interval: int) -> threading.Thread:
    """Start a daemon thread that reloads ``store`` when its file changes."""

    def _loop() -> None:
        try:
            last_mtime = store.path.stat().st_mtime
        except OSError:
            last_mtime = None
        while True:
            time.sleep(interval)
            try:
                mtime = store.path.stat().st_mtime
            except OSError:
                continue
            if mtime != last_mtime:
                store.reload()
                # Advance even on failure so a bad edit isn't retried every tick;
                # the next save changes mtime again and triggers a fresh attempt.
                last_mtime = mtime

    thread = threading.Thread(target=_loop, name="config-watcher", daemon=True)
    thread.start()
    return thread
