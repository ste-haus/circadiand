"""Endpoint behavior, error contract, and auth."""

from fastapi.testclient import TestClient

from circadiand.api import create_api
from circadiand.config import Config
from circadiand.errors import ExecutionError
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
    resp = client.post("/up", json={"hostname": "nas"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["method"] == "ipmi"  # nas default.up
    assert body["action"] == "up"
    assert body["status"] == "ok"
    assert config.hosts["nas"].methods["ipmi"].calls == [ACTION_UP]


def test_up_explicit_method(client):
    resp = client.post("/up", json={"hostname": "nas", "method": "wol"})
    assert resp.status_code == 200
    assert resp.json()["method"] == "wol"


def test_down_uses_global_default(client):
    resp = client.post("/down", json={"hostname": "workstation"})
    assert resp.status_code == 200
    assert resp.json()["method"] == "ssh"


def test_unknown_host_404(client):
    resp = client.post("/up", json={"hostname": "ghost"})
    assert resp.status_code == 404


def test_method_not_on_host_404(client):
    resp = client.post("/up", json={"hostname": "workstation", "method": "ipmi"})
    assert resp.status_code == 404


def test_unsupported_action_400(client):
    # ssh supports down, not up
    resp = client.post("/up", json={"hostname": "nas", "method": "ssh"})
    assert resp.status_code == 400


def test_no_default_400():
    host = make_host("box", [FakeMethod("wol", "box", up=True)])
    config = Config(hosts={"box": host}, defaults={})
    client = TestClient(create_api(config))
    resp = client.post("/down", json={"hostname": "box"})
    assert resp.status_code == 400


def test_driver_failure_502():
    failing = FakeMethod(
        "ssh", "box", down=True, raises=ExecutionError("ssh", "down", "timeout")
    )
    host = make_host("box", [failing], defaults={ACTION_DOWN: "ssh"})
    config = Config(hosts={"box": host}, defaults={})
    client = TestClient(create_api(config))
    resp = client.post("/down", json={"hostname": "box"})
    assert resp.status_code == 502
    assert "timeout" in resp.json()["detail"]


def test_validation_error_422(client):
    resp = client.post("/up", json={})  # missing hostname
    assert resp.status_code == 422


# --- auth --------------------------------------------------------------------

def _auth_client():
    host = make_host("box", [FakeMethod("wol", "box", up=True)], {ACTION_UP: "wol"})
    config = Config(hosts={"box": host}, defaults={})
    return TestClient(create_api(config, api_token="s3cret"))


def test_auth_required_when_token_set():
    client = _auth_client()
    assert client.post("/up", json={"hostname": "box"}).status_code == 401


def test_auth_wrong_token():
    client = _auth_client()
    resp = client.post(
        "/up", json={"hostname": "box"}, headers={"Authorization": "Bearer nope"}
    )
    assert resp.status_code == 401


def test_auth_correct_token():
    client = _auth_client()
    resp = client.post(
        "/up", json={"hostname": "box"}, headers={"Authorization": "Bearer s3cret"}
    )
    assert resp.status_code == 200


def test_openapi_schema(client):
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    assert schema["info"]["title"] == "circadiand"
    assert {"/list", "/up", "/down", "/public-key"} <= set(schema["paths"])


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
