"""Keypair resolution: env > config identity > config-dir default, plus generate."""

from pathlib import Path

import paramiko
import pytest

from circadiand import keys
from circadiand.config import Identity
from circadiand.errors import ConfigError
from circadiand.keys import ENV_SSH_PUBLIC_KEY, load_identity, resolve_keypair
from circadiand.methods.ssh import ENV_SSH_KEY

PUBLIC_KEY_TEXT = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA circadiand"
NO_IDENTITY = Identity()


def _write_keypair(directory, name="circadiand", public_text=PUBLIC_KEY_TEXT):
    private = directory / name
    public = directory / (name + ".pub")
    private.write_text("PRIVATE KEY")
    public.write_text(public_text + "\n")
    return private, public


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv(ENV_SSH_KEY, raising=False)
    monkeypatch.delenv(ENV_SSH_PUBLIC_KEY, raising=False)


# --- env mode (highest priority) ---------------------------------------------

def test_env_mode_uses_env_paths(tmp_path, monkeypatch):
    private, _ = _write_keypair(tmp_path, name="id")
    monkeypatch.setenv(ENV_SSH_KEY, str(private))
    path, text = load_identity(tmp_path, NO_IDENTITY)
    assert path == str(private)
    assert text == PUBLIC_KEY_TEXT


def test_env_mode_explicit_public_path(tmp_path, monkeypatch):
    private, _ = _write_keypair(tmp_path, name="id")
    other = tmp_path / "other.pub"
    other.write_text("ssh-rsa AAAAB circadiand\n")
    monkeypatch.setenv(ENV_SSH_KEY, str(private))
    monkeypatch.setenv(ENV_SSH_PUBLIC_KEY, str(other))
    _, text = load_identity(tmp_path, NO_IDENTITY)
    assert text == "ssh-rsa AAAAB circadiand"


def test_env_mode_overrides_config_identity(tmp_path, monkeypatch):
    env_priv, _ = _write_keypair(tmp_path, name="id")
    monkeypatch.setenv(ENV_SSH_KEY, str(env_priv))
    # config identity points elsewhere, but env still wins
    identity = Identity(private_key=str(tmp_path / "ignored"))
    path, _ = load_identity(tmp_path, identity)
    assert path == str(env_priv)


def test_env_mode_missing_file_fails(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_SSH_KEY, str(tmp_path / "nope"))
    with pytest.raises(ConfigError, match="private key not found"):
        resolve_keypair(tmp_path, NO_IDENTITY)


# --- config identity ---------------------------------------------------------

def test_config_identity_path_used(tmp_path, monkeypatch):
    key_dir = tmp_path / "keys"
    key_dir.mkdir()
    private, _ = _write_keypair(key_dir, name="mykey")
    identity = Identity(private_key=str(private))
    config_dir = tmp_path  # different from key_dir, proving identity wins
    private_path, public_path = resolve_keypair(config_dir, identity)
    assert private_path == private
    assert public_path == key_dir / "mykey.pub"


def test_config_identity_explicit_public(tmp_path):
    private = tmp_path / "k"
    private.write_text("PRIVATE")
    pub = tmp_path / "elsewhere.pub"
    pub.write_text(PUBLIC_KEY_TEXT + "\n")
    identity = Identity(private_key=str(private), public_key=str(pub))
    _, public_path = resolve_keypair(tmp_path, identity)
    assert public_path == pub


# --- default location (config dir) -------------------------------------------

def test_default_location_uses_config_dir(tmp_path):
    _write_keypair(tmp_path, name=keys.DEFAULT_KEY_NAME)
    private_path, _ = resolve_keypair(tmp_path, NO_IDENTITY)
    assert private_path == tmp_path / keys.DEFAULT_KEY_NAME


# --- generate ----------------------------------------------------------------

def test_generate_creates_loadable_keypair(tmp_path):
    config_dir = tmp_path / "config"  # does not exist yet
    private_path, text = load_identity(config_dir, NO_IDENTITY)

    assert (config_dir / keys.DEFAULT_KEY_NAME).is_file()
    assert text.startswith("ssh-ed25519 ")
    assert text.endswith(keys.KEY_COMMENT)
    paramiko.Ed25519Key.from_private_key_file(private_path)
    mode = (config_dir / keys.DEFAULT_KEY_NAME).stat().st_mode & 0o777
    assert mode == keys.PRIVATE_KEY_MODE


def test_generate_at_config_identity_path(tmp_path):
    target = tmp_path / "sub" / "myid"
    identity = Identity(private_key=str(target))
    private_path, _ = load_identity(tmp_path, identity)
    assert Path(private_path) == target
    assert target.is_file()
    assert target.with_name("myid.pub").is_file()


def test_generate_is_idempotent(tmp_path):
    result1 = load_identity(tmp_path, NO_IDENTITY)
    result2 = load_identity(tmp_path, NO_IDENTITY)
    assert result1 == result2


def test_incomplete_keypair_fails(tmp_path):
    (tmp_path / keys.DEFAULT_KEY_NAME).write_text("PRIVATE")  # private only, no .pub
    with pytest.raises(ConfigError, match="incomplete keypair"):
        resolve_keypair(tmp_path, NO_IDENTITY)
