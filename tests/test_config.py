"""Config parsing, default resolution, and fail-fast validation."""

import pytest

from circadiand.config import ensure_config, load_config, sample_config_text
from circadiand.errors import (
    ConfigError,
    HostNotFound,
    MethodNotFound,
    NoDefaultMethod,
)
from circadiand.methods.base import ACTION_DOWN, ACTION_UP


def write(tmp_path, text: str):
    path = tmp_path / "config.yaml"
    path.write_text(text)
    return path


VALID = """
defaults:
  method:
    up: wol
    down: ssh
hosts:
  nas:
    default:
      method:
        up: ipmi
        down: ssh
    methods:
      - type: wol
        mac: "aa:bb:cc:dd:ee:ff"
      - type: ipmi
        host: "10.0.0.5"
        username: admin
        password: secret
      - type: ssh
        host: "10.0.0.10"
        key_path: /keys/id
  workstation:
    methods:
      - type: wol
        mac: "11:22:33:44:55:66"
      - type: ssh
        host: "10.0.0.20"
        key_path: /keys/id
"""


def test_loads_hosts_and_methods(tmp_path):
    config = load_config(write(tmp_path, VALID))
    assert set(config.hosts) == {"nas", "workstation"}
    assert set(config.hosts["nas"].methods) == {"wol", "ipmi", "ssh"}
    assert config.defaults == {ACTION_UP: "wol", ACTION_DOWN: "ssh"}
    assert config.hosts["nas"].defaults == {ACTION_UP: "ipmi", ACTION_DOWN: "ssh"}


def test_resolution_explicit(tmp_path):
    config = load_config(write(tmp_path, VALID))
    method = config.resolve("nas", ACTION_UP, "wol")
    assert method.TYPE == "wol"


def test_resolution_host_default(tmp_path):
    config = load_config(write(tmp_path, VALID))
    method = config.resolve("nas", ACTION_UP)  # host default up = ipmi
    assert method.TYPE == "ipmi"


def test_resolution_global_default(tmp_path):
    config = load_config(write(tmp_path, VALID))
    method = config.resolve("workstation", ACTION_UP)  # global default up = wol
    assert method.TYPE == "wol"


def test_resolution_unresolved_raises(tmp_path):
    text = """
hosts:
  box:
    methods:
      - type: wol
        mac: "aa:aa:aa:aa:aa:aa"
"""
    config = load_config(write(tmp_path, text))
    with pytest.raises(NoDefaultMethod):
        config.resolve("box", ACTION_DOWN)  # no down default anywhere


def test_resolution_unknown_host(tmp_path):
    config = load_config(write(tmp_path, VALID))
    with pytest.raises(HostNotFound):
        config.resolve("nope", ACTION_UP)


def test_resolution_method_not_on_host(tmp_path):
    config = load_config(write(tmp_path, VALID))
    with pytest.raises(MethodNotFound):
        config.resolve("workstation", ACTION_UP, "ipmi")


def test_unknown_method_type_fails(tmp_path):
    text = """
hosts:
  box:
    methods:
      - type: telepathy
"""
    with pytest.raises(ConfigError, match="unknown method type"):
        load_config(write(tmp_path, text))


def test_duplicate_method_type_fails(tmp_path):
    text = """
hosts:
  box:
    methods:
      - type: wol
        mac: "aa:aa:aa:aa:aa:aa"
      - type: wol
        mac: "bb:bb:bb:bb:bb:bb"
"""
    with pytest.raises(ConfigError, match="more than once"):
        load_config(write(tmp_path, text))


def test_host_default_references_missing_method_fails(tmp_path):
    text = """
hosts:
  box:
    default:
      method:
        up: ipmi
    methods:
      - type: wol
        mac: "aa:aa:aa:aa:aa:aa"
"""
    with pytest.raises(ConfigError, match="not defined on the host"):
        load_config(write(tmp_path, text))


def test_missing_required_method_key_fails(tmp_path):
    text = """
hosts:
  box:
    methods:
      - type: wol
"""
    with pytest.raises(ConfigError, match="missing required key 'mac'"):
        load_config(write(tmp_path, text))


def test_unknown_default_action_fails(tmp_path):
    text = """
defaults:
  method:
    sideways: wol
hosts:
  box:
    methods:
      - type: wol
        mac: "aa:aa:aa:aa:aa:aa"
"""
    with pytest.raises(ConfigError, match="unknown default action"):
        load_config(write(tmp_path, text))


def test_global_default_unknown_type_fails(tmp_path):
    text = """
defaults:
  method:
    up: telepathy
hosts:
  box:
    methods:
      - type: wol
        mac: "aa:aa:aa:aa:aa:aa"
"""
    with pytest.raises(ConfigError, match="unknown method type"):
        load_config(write(tmp_path, text))


def test_identity_section_parsed(tmp_path):
    text = """
identity:
  private_key: /keys/circadiand
  public_key: /keys/circadiand.pub
hosts:
  box:
    methods:
      - type: wol
        mac: "aa:aa:aa:aa:aa:aa"
"""
    config = load_config(write(tmp_path, text))
    assert config.identity.private_key == "/keys/circadiand"
    assert config.identity.public_key == "/keys/circadiand.pub"


def test_identity_defaults_to_empty(tmp_path):
    config = load_config(write(tmp_path, VALID))
    assert config.identity.private_key is None
    assert config.identity.public_key is None


def test_identity_must_be_mapping(tmp_path):
    text = """
identity: "/keys/circadiand"
hosts:
  box:
    methods:
      - type: wol
        mac: "aa:aa:aa:aa:aa:aa"
"""
    with pytest.raises(ConfigError, match="'identity' must be a mapping"):
        load_config(write(tmp_path, text))


def test_identity_path_must_be_string(tmp_path):
    text = """
identity:
  private_key: 42
hosts:
  box:
    methods:
      - type: wol
        mac: "aa:aa:aa:aa:aa:aa"
"""
    with pytest.raises(ConfigError, match="must be a string path"):
        load_config(write(tmp_path, text))


def test_no_hosts_fails(tmp_path):
    with pytest.raises(ConfigError, match="hosts"):
        load_config(write(tmp_path, "defaults: {}\n"))


def test_missing_file_fails(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "does-not-exist.yaml")


def test_ensure_config_creates_demo_when_missing(tmp_path):
    target = tmp_path / "sub" / "config.yaml"  # parent dir doesn't exist yet
    created = ensure_config(target)
    assert created is True
    assert target.is_file()
    assert target.read_text() == sample_config_text()
    # the generated demo must be a valid, loadable config
    config = load_config(target)
    assert "nas" in config.hosts


def test_ensure_config_does_not_overwrite_existing(tmp_path):
    target = tmp_path / "config.yaml"
    target.write_text("hosts:\n  keep:\n    methods:\n      - {type: wol, mac: a}\n")
    created = ensure_config(target)
    assert created is False
    assert "keep" in target.read_text()


def test_malformed_yaml_raises_config_error(tmp_path):
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config(write(tmp_path, "hosts: [unbalanced\n"))
