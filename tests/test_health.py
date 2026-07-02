"""HealthMonitor probe logic and worker reconciliation (no real network I/O)."""

import time

from circadiand.config import Config, Health
from circadiand.errors import ExecutionError
from circadiand.health import HEALTH_ALIVE, HEALTH_DEAD, HEALTH_UNKNOWN, HealthMonitor
from circadiand.reload import ConfigStore

from .conftest import FakeMethod, make_host


def _wait_for_status(monitor, hostname, timeout=2.0):
    """Poll until the worker thread has recorded a status (or time out)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = monitor.get(hostname)
        if status is not None:
            return status
        time.sleep(0.01)
    return None

# Large interval: workers run one immediate check then park, so tests inspect
# bookkeeping/status without racing a second probe.
PARKED_INTERVAL = 3600


def _config(alive=True, raises=None, interval=PARKED_INTERVAL) -> Config:
    method = FakeMethod("ping", "nas", check=True, alive=alive, raises=raises)
    host = make_host("nas", [method])
    host.health = Health(type="ping", interval=interval)
    return Config(hosts={"nas": host}, defaults={})


# --- probe logic (called directly, no threads) -------------------------------

def test_run_check_alive():
    monitor = HealthMonitor(_config(alive=True))
    monitor._run_check("nas")
    status = monitor.get("nas")
    assert status.state == HEALTH_ALIVE
    assert status.method == "ping"
    assert status.interval == PARKED_INTERVAL
    assert status.checked_at is not None
    assert status.detail is None


def test_run_check_dead():
    monitor = HealthMonitor(_config(alive=False))
    monitor._run_check("nas")
    status = monitor.get("nas")
    assert status.state == HEALTH_DEAD
    assert "nas" in status.detail


def test_run_check_unknown_on_probe_error():
    monitor = HealthMonitor(_config(raises=ExecutionError("ping", "check", "boom")))
    monitor._run_check("nas")
    status = monitor.get("nas")
    assert status.state == HEALTH_UNKNOWN
    assert "boom" in status.detail


# --- reconciliation ----------------------------------------------------------

def test_reconcile_starts_worker_and_probes():
    store = ConfigStore("/nonexistent", _config())
    monitor = HealthMonitor(store)
    monitor.start()
    try:
        assert "nas" in monitor._workers
        status = _wait_for_status(monitor, "nas")  # immediate first probe
        assert status is not None and status.state == HEALTH_ALIVE
    finally:
        monitor.stop()


def test_reconcile_stops_worker_and_drops_status_when_health_removed():
    store = ConfigStore("/nonexistent", _config())
    monitor = HealthMonitor(store)
    monitor.start()
    try:
        assert "nas" in monitor._workers
        # Swap in a config where nas has no health configured.
        host = make_host("nas", [FakeMethod("ping", "nas", check=True)])
        store._config = Config(hosts={"nas": host}, defaults={})
        monitor.reconcile()
        assert "nas" not in monitor._workers
        assert monitor.get("nas") is None
    finally:
        monitor.stop()


def test_reconcile_restarts_worker_on_interval_change():
    store = ConfigStore("/nonexistent", _config(interval=PARKED_INTERVAL))
    monitor = HealthMonitor(store)
    monitor.start()
    try:
        first = monitor._workers["nas"]
        store._config = _config(interval=PARKED_INTERVAL // 2)
        monitor.reconcile()
        second = monitor._workers["nas"]
        assert second is not first
        assert second.interval == PARKED_INTERVAL // 2
    finally:
        monitor.stop()


def test_reconcile_skips_inapplicable_global_health():
    # Global health names ping, but this host has no ping method -> skip, no worker.
    host = make_host("vm", [FakeMethod("wol", "vm", up=True)])
    config = Config(
        hosts={"vm": host}, defaults={}, health=Health(type="ping", interval=PARKED_INTERVAL)
    )
    monitor = HealthMonitor(config)
    monitor.start()
    try:
        assert "vm" not in monitor._workers
        assert monitor.get("vm") is None
    finally:
        monitor.stop()


def test_start_registers_reload_listener():
    store = ConfigStore("/nonexistent", _config())
    monitor = HealthMonitor(store)
    monitor.start()
    try:
        assert monitor.reconcile in store._listeners
    finally:
        monitor.stop()
