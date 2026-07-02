"""Endpoint behavior, error contract, and auth."""

from fastapi.testclient import TestClient

from circadiand.api import create_api
from circadiand.config import Config
from circadiand.errors import ExecutionError
from circadiand.health import (
    HEALTH_ALIVE,
    HEALTH_DEAD,
    HEALTH_UNKNOWN,
    HealthSample,
    HealthStatus,
)
from circadiand.methods.base import ACTION_DOWN, ACTION_UP

from .conftest import FAKE_PUBLIC_KEY, FakeMethod, make_host


def test_list(client):
    resp = client.get("/list")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"nas", "workstation"}

    nas = body["nas"]
    types = {m["type"]: m["actions"] for m in nas["methods"]}
    assert types["wol"] == ["up"]
    assert types["ssh"] == ["down"]
    assert nas["defaults"] == {"up": "ipmi", "down": "ssh"}

    # workstation has no per-host default -> resolves from the global defaults
    assert body["workstation"]["defaults"] == {"up": "wol", "down": "ssh"}


def test_list_leaks_no_secrets(client):
    # FakeMethods carry no secrets, but the contract is that /list exposes only
    # type/actions/defaults — never connection details.
    raw = client.get("/list").text
    assert "password" not in raw
    assert "mac" not in raw


def test_up_uses_host_default(client, config):
    resp = client.post("/nas/up")
    assert resp.status_code == 200
    body = resp.json()
    assert body["method"] == "ipmi"  # nas default.up
    assert body["action"] == "up"
    assert body["status"] == "ok"
    assert config.hosts["nas"].methods["ipmi"].calls == [ACTION_UP]


def test_up_explicit_method(client):
    resp = client.post("/nas/up", params={"method": "wol"})
    assert resp.status_code == 200
    assert resp.json()["method"] == "wol"


def test_down_uses_global_default(client):
    resp = client.post("/workstation/down")
    assert resp.status_code == 200
    assert resp.json()["method"] == "ssh"


def test_unknown_host_404(client):
    resp = client.post("/ghost/up")
    assert resp.status_code == 404


def test_method_not_on_host_404(client):
    resp = client.post("/workstation/up", params={"method": "ipmi"})
    assert resp.status_code == 404


def test_unsupported_action_400(client):
    # ssh supports down, not up
    resp = client.post("/nas/up", params={"method": "ssh"})
    assert resp.status_code == 400


def test_invalid_action_422(client):
    resp = client.post("/nas/sideways")
    assert resp.status_code == 422


def test_no_default_400():
    host = make_host("box", [FakeMethod("wol", "box", up=True)])
    config = Config(hosts={"box": host}, defaults={})
    client = TestClient(create_api(config))
    resp = client.post("/box/down")
    assert resp.status_code == 400


def test_driver_failure_502():
    failing = FakeMethod(
        "ssh", "box", down=True, raises=ExecutionError("ssh", "down", "timeout")
    )
    host = make_host("box", [failing], defaults={ACTION_DOWN: "ssh"})
    config = Config(hosts={"box": host}, defaults={})
    client = TestClient(create_api(config))
    resp = client.post("/box/down")
    assert resp.status_code == 502
    assert "timeout" in resp.json()["detail"]


# --- auth --------------------------------------------------------------------

def _auth_client():
    host = make_host("box", [FakeMethod("wol", "box", up=True)], {ACTION_UP: "wol"})
    config = Config(hosts={"box": host}, defaults={})
    return TestClient(create_api(config, api_token="s3cret"))


def test_auth_required_when_token_set():
    client = _auth_client()
    assert client.post("/box/up").status_code == 401


def test_auth_wrong_token():
    client = _auth_client()
    resp = client.post("/box/up", headers={"Authorization": "Bearer nope"})
    assert resp.status_code == 401


def test_auth_correct_token():
    client = _auth_client()
    resp = client.post("/box/up", headers={"Authorization": "Bearer s3cret"})
    assert resp.status_code == 200


def test_openapi_schema(client):
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    assert schema["info"]["title"] == "circadiand"
    assert {"/list", "/public-key", "/{hostname}/{action}"} <= set(schema["paths"])


# --- public key --------------------------------------------------------------

def test_public_key_returns_plaintext(client):
    resp = client.get("/public-key")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert resp.text == FAKE_PUBLIC_KEY


def test_public_key_unauthenticated_even_with_token():
    host = make_host("box", [FakeMethod("wol", "box", up=True)], {ACTION_UP: "wol"})
    config = Config(hosts={"box": host}, defaults={})
    client = TestClient(
        create_api(config, api_token="s3cret", public_key=FAKE_PUBLIC_KEY)
    )
    # No Authorization header — public key must still be served.
    resp = client.get("/public-key")
    assert resp.status_code == 200
    assert resp.text == FAKE_PUBLIC_KEY


def test_public_key_404_when_unconfigured():
    host = make_host("box", [FakeMethod("wol", "box", up=True)], {ACTION_UP: "wol"})
    config = Config(hosts={"box": host}, defaults={})
    client = TestClient(create_api(config))  # no public_key
    assert client.get("/public-key").status_code == 404


# --- live reload via ConfigStore ---------------------------------------------

def test_api_reflects_configstore_swap(tmp_path):
    from circadiand.reload import ConfigStore

    one = make_host("alpha", [FakeMethod("wol", "alpha", up=True)], {ACTION_UP: "wol"})
    store = ConfigStore(tmp_path / "config.yaml", Config(hosts={"alpha": one}, defaults={}))
    client = TestClient(create_api(store))

    assert set(client.get("/list").json()) == {"alpha"}

    # Swap the live config the way reload() does internally.
    two_a = make_host("alpha", [FakeMethod("wol", "alpha", up=True)], {ACTION_UP: "wol"})
    two_b = make_host("beta", [FakeMethod("wol", "beta", up=True)], {ACTION_UP: "wol"})
    store._config = Config(hosts={"alpha": two_a, "beta": two_b}, defaults={})

    assert set(client.get("/list").json()) == {"alpha", "beta"}


# --- health status: GET /{hostname} ------------------------------------------

class _FakeMonitor:
    def __init__(self, statuses: dict[str, HealthStatus]):
        self._statuses = statuses

    def get(self, hostname):
        return self._statuses.get(hostname)


def _health_client(config, statuses):
    return TestClient(create_api(config, health_monitor=_FakeMonitor(statuses)))


def test_health_alive_200(config):
    status = HealthStatus(HEALTH_ALIVE, "ping", 5, "2026-07-01T00:00:00+00:00")
    resp = _health_client(config, {"nas": status}).get("/nas")
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "alive"
    assert body["method"] == "ping"
    assert body["interval"] == 5
    assert body["detail"] is None


def test_health_dead_503_with_detail(config):
    status = HealthStatus(
        HEALTH_DEAD, "ping", 5, "2026-07-01T00:00:00+00:00",
        "nas is not responding to ping",
    )
    resp = _health_client(config, {"nas": status}).get("/nas")
    assert resp.status_code == 503
    assert "not responding" in resp.json()["detail"]


def test_health_unknown_503(config):
    status = HealthStatus(HEALTH_UNKNOWN, "ping", 5, None, "permission denied")
    resp = _health_client(config, {"nas": status}).get("/nas")
    assert resp.status_code == 503
    assert resp.json()["state"] == "unknown"


def test_health_unknown_host_404(config):
    resp = _health_client(config, {}).get("/ghost")
    assert resp.status_code == 404


def test_health_not_configured_404(config):
    # nas exists but has no health status recorded.
    resp = _health_client(config, {}).get("/nas")
    assert resp.status_code == 404


def test_health_no_monitor_404(client):
    # The default client fixture wires no health monitor.
    assert client.get("/nas").status_code == 404


def test_health_includes_recent_samples(config):
    samples = [
        HealthSample(HEALTH_DEAD, "2026-07-01T00:00:00+00:00", "was down"),
        HealthSample(HEALTH_ALIVE, "2026-07-01T00:00:10+00:00"),
    ]
    status = HealthStatus(
        HEALTH_ALIVE, "ping", 5, "2026-07-01T00:00:10+00:00", None, samples
    )
    resp = _health_client(config, {"nas": status}).get("/nas")
    assert resp.status_code == 200
    body = resp.json()
    assert [s["state"] for s in body["samples"]] == ["dead", "alive"]
    assert body["samples"][0]["detail"] == "was down"
    assert body["samples"][0]["checked_at"] == "2026-07-01T00:00:00+00:00"


def test_health_route_does_not_shadow_list(config):
    # /list must still resolve to the list handler, not the health handler.
    resp = _health_client(config, {}).get("/list")
    assert resp.status_code == 200
    assert set(resp.json()) == {"nas", "workstation"}
