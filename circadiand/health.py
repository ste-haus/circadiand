"""Background liveliness monitoring.

``HealthMonitor`` runs one daemon worker thread per monitored host. Each worker
probes its host with the configured ``health`` method (see
:meth:`~circadiand.config.Config.resolve_health`), then sleeps for the host's
interval, writing the latest result into a shared status map the API reads.

The monitor reads config through the same :class:`~circadiand.reload.ConfigStore`
the API uses, and registers :meth:`reconcile` as a reload listener so workers are
started, stopped, and restarted when hosts or their health config change — no
restart required.
"""

import dataclasses
import logging
import threading
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional, Union

from .config import Config
from .reload import ConfigStore

HEALTH_ALIVE = "alive"
HEALTH_DEAD = "dead"
HEALTH_UNKNOWN = "unknown"

# Per-host rolling history: keep at most the last hour of probes, capped at 100
# samples (whichever bound is hit first — 100 covers a full hour at intervals
# >= 36s; faster intervals are capped by count).
MAX_SAMPLES = 100
MAX_SAMPLE_AGE_SECONDS = 3600

_LOGGER = logging.getLogger("circadiand")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclasses.dataclass
class HealthSample:
    state: str                      # alive | dead | unknown
    checked_at: str                 # UTC ISO-8601 of the probe
    detail: Optional[str] = None    # down/error message when not alive


@dataclasses.dataclass
class HealthStatus:
    state: str                      # alive | dead | unknown
    method: str                     # method type used to probe
    interval: int                   # probe interval in seconds
    checked_at: Optional[str]       # UTC ISO-8601 of the last probe
    detail: Optional[str] = None    # down/error message when not alive
    # Recent probes, oldest first (bounded to the last hour / MAX_SAMPLES).
    samples: list[HealthSample] = dataclasses.field(default_factory=list)


class _Worker:
    """A daemon thread that probes one host on its interval until stopped."""

    def __init__(self, hostname: str, method_type: str, interval: int,
                 run_check: Callable[[str], None]):
        self.hostname = hostname
        self.method_type = method_type
        self.interval = interval
        self._run_check = run_check
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, name=f"health-{hostname}", daemon=True
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._run_check(self.hostname)
            # Event-based wait so a stop is honored promptly between probes.
            self._stop.wait(self.interval)


class HealthMonitor:
    """Owns the per-host health workers and the status map the API reads."""

    def __init__(self, source: Union[Config, ConfigStore]):
        self._store = source if isinstance(source, ConfigStore) else None
        self._fixed = None if self._store is not None else source
        self._status: dict[str, HealthStatus] = {}
        self._history: dict[str, deque[HealthSample]] = {}
        self._workers: dict[str, _Worker] = {}
        self._warned: set[str] = set()
        self._lock = threading.Lock()

    def _current(self) -> Config:
        return self._store.config if self._store is not None else self._fixed

    @staticmethod
    def _prune(history: "deque[HealthSample]") -> None:
        """Drop samples older than the retention window (count is capped by the
        deque's maxlen). Must be called with the lock held."""
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=MAX_SAMPLE_AGE_SECONDS)
        while history and datetime.fromisoformat(history[0].checked_at) < cutoff:
            history.popleft()

    def get(self, hostname: str) -> Optional[HealthStatus]:
        with self._lock:
            status = self._status.get(hostname)
            if status is None:
                return None
            history = self._history.get(hostname)
            if history is not None:
                self._prune(history)
            samples = list(history) if history else []
            return dataclasses.replace(status, samples=samples)

    def start(self) -> None:
        """Begin monitoring and reconcile on every future config reload."""
        if self._store is not None:
            self._store.add_listener(self.reconcile)
        self.reconcile()

    def stop(self) -> None:
        with self._lock:
            for worker in self._workers.values():
                worker.stop()
            self._workers.clear()

    def _run_check(self, hostname: str) -> None:
        """Probe one host once and record the result. Never raises."""
        resolved = self._current().resolve_health(hostname)
        if resolved is None:
            # No longer monitored (config changed under us) — drop stale state.
            with self._lock:
                self._status.pop(hostname, None)
                self._history.pop(hostname, None)
            return
        method, interval = resolved
        try:
            alive = method.check()
        except Exception as exc:  # a probe that can't run -> unknown, worker lives
            state, detail = HEALTH_UNKNOWN, str(exc)
        else:
            if alive:
                state, detail = HEALTH_ALIVE, None
            else:
                state, detail = HEALTH_DEAD, f"{hostname} is not responding to {method.TYPE}"

        checked_at = _now_iso()
        status = HealthStatus(
            state=state, method=method.TYPE, interval=interval,
            checked_at=checked_at, detail=detail,
        )
        sample = HealthSample(state=state, checked_at=checked_at, detail=detail)
        with self._lock:
            self._status[hostname] = status
            history = self._history.setdefault(hostname, deque(maxlen=MAX_SAMPLES))
            history.append(sample)
            self._prune(history)

    def reconcile(self) -> None:
        """Align running workers with the hosts the current config wants probed."""
        active = self._current()
        desired: dict[str, tuple[str, int]] = {}
        for hostname, host in active.hosts.items():
            resolved = active.resolve_health(hostname)
            if resolved is not None:
                method, interval = resolved
                desired[hostname] = (method.TYPE, interval)
                self._warned.discard(hostname)
            elif (host.health or active.health) is not None:
                # Health configured but not usable on this host (a global default
                # naming a method the host lacks / can't check). Warn once.
                if hostname not in self._warned:
                    health = host.health or active.health
                    _LOGGER.warning(
                        "host '%s' health type '%s' is not usable "
                        "(method not defined or can't check); skipping",
                        hostname, health.type,
                    )
                    self._warned.add(hostname)

        with self._lock:
            for hostname in list(self._workers):
                worker = self._workers[hostname]
                signature = desired.get(hostname)
                if signature != (worker.method_type, worker.interval):
                    worker.stop()
                    del self._workers[hostname]
                    if signature is None:
                        self._status.pop(hostname, None)
                        self._history.pop(hostname, None)

            for hostname, (method_type, interval) in desired.items():
                if hostname not in self._workers:
                    worker = _Worker(hostname, method_type, interval, self._run_check)
                    self._workers[hostname] = worker
                    worker.start()
