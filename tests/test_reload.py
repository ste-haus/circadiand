"""Config live-reload: ConfigStore and the polling watcher."""

import time

from circadiand.config import load_config
from circadiand.reload import ConfigStore, start_config_watcher

ONE_HOST = """
defaults:
  power:
    up: wol
hosts:
  alpha:
    methods:
      - type: wol
        mac: "aa:aa:aa:aa:aa:aa"
"""

TWO_HOSTS = """
defaults:
  power:
    up: wol
hosts:
  alpha:
    methods:
      - type: wol
        mac: "aa:aa:aa:aa:aa:aa"
  beta:
    methods:
      - type: wol
        mac: "bb:bb:bb:bb:bb:bb"
"""

BROKEN = "this: is: not: valid: yaml: structure\nhosts: []\n"


def _store(tmp_path, text):
    path = tmp_path / "config.yaml"
    path.write_text(text)
    return path, ConfigStore(path, load_config(path))


def test_reload_picks_up_changes(tmp_path):
    path, store = _store(tmp_path, ONE_HOST)
    assert set(store.config.hosts) == {"alpha"}

    path.write_text(TWO_HOSTS)
    assert store.reload() is True
    assert set(store.config.hosts) == {"alpha", "beta"}


def test_reload_keeps_old_config_on_error(tmp_path):
    path, store = _store(tmp_path, TWO_HOSTS)
    assert set(store.config.hosts) == {"alpha", "beta"}

    path.write_text(BROKEN)
    assert store.reload() is False
    # previous good config is retained
    assert set(store.config.hosts) == {"alpha", "beta"}


def test_watcher_reloads_on_file_change(tmp_path):
    path, store = _store(tmp_path, ONE_HOST)
    start_config_watcher(store, interval=1)  # daemon thread, polls ~1s

    # mtime resolution can be coarse; make the change unmistakable.
    time.sleep(0.05)
    path.write_text(TWO_HOSTS)

    deadline = time.time() + 5
    while time.time() < deadline:
        if set(store.config.hosts) == {"alpha", "beta"}:
            break
        time.sleep(0.1)
    assert set(store.config.hosts) == {"alpha", "beta"}
