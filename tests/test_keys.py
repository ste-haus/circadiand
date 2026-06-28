"""Keypair resolution: env > /config file > generate, plus validation."""

import paramiko
import pytest

from circadiand import keys
from circadiand.errors import ConfigError
from circadiand.keys import ENV_SSH_PUBLIC_KEY, load_identity, resolve_keypair
from circadiand.methods.ssh import ENV_SSH_KEY

PUBLIC_KEY_TEXT = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA circadiand"


def _write_keypair(directory, name="id", public_text=PUBLIC_KEY_TEXT):
    private = directory / name
    public = directory / (name + ".pub")
    private.write_text("PRIVATE KEY")
    public.write_text(public_text + "\n")
    return private, public


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv(ENV_SSH_KEY, raising=False)
    monkeypatch.delenv(ENV_SSH_PUBLIC_KEY, raising=False)


# --- env mode ----------------------------------------------------------------

def test_env_mode_uses_env_paths(tmp_path, monkeypatch):
    private, _ = _write_keypair(tmp_path)
    monkeypatch.setenv(ENV_SSH_KEY, str(private))
    path, text = load_identity()
    assert path == str(private)
    assert text == PUBLIC_KEY_TEXT  # trailing newline stripped


def test_env_mode_explicit_public_path(tmp_path, monkeypatch):
    private, _ = _write_keypair(tmp_path)
    other = tmp_path / "other.pub"
    other.write_text("ssh-rsa AAAAB circadiand\n")
    monkeypatch.setenv(ENV_SSH_KEY, str(private))
    monkeypatch.setenv(ENV_SSH_PUBLIC_KEY, str(other))
    _, text = load_identity()
    assert text == "ssh-rsa AAAAB circadiand"


def test_env_mode_missing_private_file_fails(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_SSH_KEY, str(tmp_path / "nope"))
    with pytest.raises(ConfigError, match="private key not found"):
        resolve_keypair()


def test_env_mode_missing_public_file_fails(tmp_path, monkeypatch):
    private = tmp_path / "id"
    private.write_text("PRIVATE KEY")  # no matching .pub
    monkeypatch.setenv(ENV_SSH_KEY, str(private))
    with pytest.raises(ConfigError, match="public key not found"):
        resolve_keypair()


# --- file mode (default /config location) ------------------------------------

def test_file_mode_uses_existing_default(tmp_path, monkeypatch):
    monkeypatch.setattr(keys, "DEFAULT_KEY_DIR", str(tmp_path))
    _write_keypair(tmp_path, name=keys.DEFAULT_KEY_NAME)
    private_path, public_path = resolve_keypair()
    assert private_path == tmp_path / keys.DEFAULT_KEY_NAME
    assert public_path.read_text().strip() == PUBLIC_KEY_TEXT


# --- generate mode -----------------------------------------------------------

def test_generate_mode_creates_loadable_keypair(tmp_path, monkeypatch):
    key_dir = tmp_path / "config"  # does not exist yet
    monkeypatch.setattr(keys, "DEFAULT_KEY_DIR", str(key_dir))

    private_path, text = load_identity()

    assert (key_dir / keys.DEFAULT_KEY_NAME).is_file()
    assert (key_dir / (keys.DEFAULT_KEY_NAME + ".pub")).is_file()
    assert text.startswith("ssh-ed25519 ")
    assert text.endswith(keys.KEY_COMMENT)
    # private key must be a real, loadable ed25519 key
    paramiko.Ed25519Key.from_private_key_file(private_path)
    # private key permissions locked down
    assert (key_dir / keys.DEFAULT_KEY_NAME).stat().st_mode & 0o777 == keys.PRIVATE_KEY_MODE


def test_generate_is_idempotent_once_present(tmp_path, monkeypatch):
    monkeypatch.setattr(keys, "DEFAULT_KEY_DIR", str(tmp_path))
    path1, text1 = load_identity()          # generates
    path2, text2 = load_identity()          # reuses
    assert (path1, text1) == (path2, text2)
