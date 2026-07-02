"""Registry behavior and per-driver argument building (mocked I/O)."""

import types

import pytest

from circadiand.errors import ConfigError, ExecutionError, UnsupportedAction
from circadiand.methods import METHOD_REGISTRY
from circadiand.methods.ipmi import IpmiMethod
from circadiand.methods.ping import PingMethod
from circadiand.methods.ssh import ENV_SSH_KEY, SshMethod
from circadiand.methods.wol import WOL_PACKET_INTERVAL_SECONDS, WolMethod


def test_registry_populated():
    assert set(METHOD_REGISTRY) == {"wol", "ipmi", "ssh", "ping"}
    assert METHOD_REGISTRY["wol"] is WolMethod


def test_support_flags():
    assert WolMethod.SUPPORTS_UP and not WolMethod.SUPPORTS_DOWN
    assert IpmiMethod.SUPPORTS_UP and not IpmiMethod.SUPPORTS_DOWN
    assert SshMethod.SUPPORTS_DOWN and not SshMethod.SUPPORTS_UP
    assert PingMethod.SUPPORTS_CHECK
    assert not PingMethod.SUPPORTS_UP and not PingMethod.SUPPORTS_DOWN


def test_shared_host_flags():
    # ssh/ping target the machine's primary address; ipmi/wol carry their own.
    assert SshMethod.USES_SHARED_HOST and PingMethod.USES_SHARED_HOST
    assert not IpmiMethod.USES_SHARED_HOST and not WolMethod.USES_SHARED_HOST


def test_unsupported_action_raises():
    wol = WolMethod("box", mac="aa:bb:cc:dd:ee:ff")
    with pytest.raises(UnsupportedAction):
        wol.power_down()


def test_base_check_unsupported_by_default():
    wol = WolMethod("box", mac="aa:bb:cc:dd:ee:ff")
    with pytest.raises(UnsupportedAction):
        wol.check()


# --- WOL ---------------------------------------------------------------------

def test_wol_sends_magic_packet(monkeypatch):
    calls = {}

    def fake_send(mac, **kwargs):
        calls["mac"] = mac
        calls["kwargs"] = kwargs

    monkeypatch.setattr("circadiand.methods.wol.wakeonlan.send_magic_packet", fake_send)

    wol = WolMethod("box", mac="aa:bb:cc:dd:ee:ff", broadcast="192.168.1.255", port=7)
    result = wol.power_up()

    assert calls["mac"] == "aa:bb:cc:dd:ee:ff"
    assert calls["kwargs"] == {"ip_address": "192.168.1.255", "port": 7}
    assert "aa:bb:cc:dd:ee:ff" in result


def test_wol_default_count(monkeypatch):
    sends = []
    monkeypatch.setattr(
        "circadiand.methods.wol.wakeonlan.send_magic_packet",
        lambda mac, **kwargs: sends.append(mac),
    )
    monkeypatch.setattr("circadiand.methods.wol.time.sleep", lambda _: None)

    wol = WolMethod("box", mac="aa:bb:cc:dd:ee:ff")
    wol.power_up()

    assert len(sends) == 10


def test_wol_count_override(monkeypatch):
    sends = []
    monkeypatch.setattr(
        "circadiand.methods.wol.wakeonlan.send_magic_packet",
        lambda mac, **kwargs: sends.append(mac),
    )
    monkeypatch.setattr("circadiand.methods.wol.time.sleep", lambda _: None)

    wol = WolMethod("box", mac="aa:bb:cc:dd:ee:ff", count=3)
    result = wol.power_up()

    assert len(sends) == 3
    assert "3" in result


def test_wol_delays_between_packets(monkeypatch):
    sleeps = []
    monkeypatch.setattr(
        "circadiand.methods.wol.wakeonlan.send_magic_packet",
        lambda mac, **kwargs: None,
    )
    monkeypatch.setattr(
        "circadiand.methods.wol.time.sleep", lambda seconds: sleeps.append(seconds)
    )

    wol = WolMethod("box", mac="aa:bb:cc:dd:ee:ff", count=3)
    wol.power_up()

    # One 100ms gap between each pair of packets, none before the first.
    assert sleeps == [WOL_PACKET_INTERVAL_SECONDS] * 2


def test_wol_requires_mac():
    with pytest.raises(ConfigError, match="mac"):
        WolMethod("box")


# --- Ping --------------------------------------------------------------------

def test_ping_requires_host():
    with pytest.raises(ConfigError, match="host"):
        PingMethod("box")


def test_ping_alive(monkeypatch):
    calls = {}

    def fake_ping(address, **kwargs):
        calls["address"] = address
        calls["kwargs"] = kwargs
        return types.SimpleNamespace(is_alive=True)

    monkeypatch.setattr("circadiand.methods.ping.icmplib.ping", fake_ping)

    ping = PingMethod("box", host="10.0.0.9", count=2, timeout=1)
    assert ping.check() is True
    assert calls["address"] == "10.0.0.9"
    assert calls["kwargs"] == {"count": 2, "timeout": 1.0, "privileged": True}


def test_ping_dead(monkeypatch):
    monkeypatch.setattr(
        "circadiand.methods.ping.icmplib.ping",
        lambda address, **kwargs: types.SimpleNamespace(is_alive=False),
    )
    ping = PingMethod("box", host="10.0.0.9")
    assert ping.check() is False


def test_ping_probe_error_wrapped(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr("circadiand.methods.ping.icmplib.ping", boom)
    ping = PingMethod("box", host="10.0.0.9")
    with pytest.raises(ExecutionError, match="permission denied"):
        ping.check()


# --- IPMI --------------------------------------------------------------------

class _FakeIpmiCommand:
    instances: list["_FakeIpmiCommand"] = []

    def __init__(self, bmc, userid, password):
        self.bmc = bmc
        self.userid = userid
        self.password = password
        self.power = None
        self.logged_out = False
        self.ipmi_session = types.SimpleNamespace(logout=self._logout)
        _FakeIpmiCommand.instances.append(self)

    def _logout(self):
        self.logged_out = True

    def set_power(self, state, wait=False):
        self.power = state


def test_ipmi_power_up(monkeypatch):
    _FakeIpmiCommand.instances.clear()
    monkeypatch.setattr(
        "circadiand.methods.ipmi.ipmi_command.Command", _FakeIpmiCommand
    )

    ipmi = IpmiMethod("box", host="10.0.0.5", username="admin", password="secret")
    result = ipmi.power_up()

    cmd = _FakeIpmiCommand.instances[-1]
    assert (cmd.bmc, cmd.userid, cmd.password) == ("10.0.0.5", "admin", "secret")
    assert cmd.power == "on"
    assert cmd.logged_out
    assert "10.0.0.5" in result


def test_ipmi_driver_failure_wrapped(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("bmc unreachable")

    monkeypatch.setattr("circadiand.methods.ipmi.ipmi_command.Command", boom)
    ipmi = IpmiMethod("box", host="10.0.0.5", username="admin", password="secret")
    with pytest.raises(ExecutionError, match="bmc unreachable"):
        ipmi.power_up()


def test_ipmi_requires_credentials():
    with pytest.raises(ConfigError):
        IpmiMethod("box", host="10.0.0.5")


# --- SSH ---------------------------------------------------------------------

class _FakeChannel:
    def __init__(self, status):
        self._status = status

    def recv_exit_status(self):
        return self._status


class _FakeStream:
    def __init__(self, status=0, data=b""):
        self.channel = _FakeChannel(status)
        self._data = data

    def read(self):
        return self._data


class _FakeSSHClient:
    exit_status = 0
    stderr_data = b""
    last: dict = {}

    def set_missing_host_key_policy(self, policy):
        pass

    def connect(self, **kwargs):
        _FakeSSHClient.last = {"connect": kwargs}

    def exec_command(self, command, timeout=None):
        _FakeSSHClient.last["command"] = command
        return None, _FakeStream(self.exit_status), _FakeStream(0, self.stderr_data)

    def close(self):
        _FakeSSHClient.last["closed"] = True


def test_ssh_power_down(monkeypatch):
    _FakeSSHClient.exit_status = 0
    monkeypatch.setattr("circadiand.methods.ssh.paramiko.SSHClient", _FakeSSHClient)

    ssh = SshMethod(
        "box", host="10.0.0.10", username="circadiand", key_path="/keys/id"
    )
    result = ssh.power_down()

    connect = _FakeSSHClient.last["connect"]
    assert connect["hostname"] == "10.0.0.10"
    assert connect["username"] == "circadiand"
    assert connect["key_filename"] == "/keys/id"
    assert _FakeSSHClient.last["command"] == "sudo shutdown -h now"
    assert _FakeSSHClient.last["closed"]
    assert "10.0.0.10" in result


def test_ssh_nonzero_exit_raises(monkeypatch):
    _FakeSSHClient.exit_status = 1
    _FakeSSHClient.stderr_data = b"permission denied"
    monkeypatch.setattr("circadiand.methods.ssh.paramiko.SSHClient", _FakeSSHClient)

    ssh = SshMethod("box", host="10.0.0.10", key_path="/keys/id")
    with pytest.raises(ExecutionError, match="permission denied"):
        ssh.power_down()

    _FakeSSHClient.exit_status = 0
    _FakeSSHClient.stderr_data = b""


def test_ssh_key_path_from_env(monkeypatch):
    # No explicit key_path -> resolved from the env identity at call time.
    monkeypatch.setenv(ENV_SSH_KEY, "/env/key")
    ssh = SshMethod("box", host="10.0.0.10")
    assert ssh._resolve_key_path() == "/env/key"


def test_ssh_explicit_key_path_wins(monkeypatch):
    monkeypatch.setenv(ENV_SSH_KEY, "/env/key")
    ssh = SshMethod("box", host="10.0.0.10", key_path="/explicit/key")
    assert ssh._resolve_key_path() == "/explicit/key"


def test_ssh_missing_key_errors_at_call_time(monkeypatch):
    monkeypatch.delenv(ENV_SSH_KEY, raising=False)
    ssh = SshMethod("box", host="10.0.0.10")  # construction succeeds
    with pytest.raises(ExecutionError, match="no SSH key configured"):
        ssh.power_down()
