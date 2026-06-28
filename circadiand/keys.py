"""Resolve, generate, and validate the circadiand SSH identity keypair.

circadiand authenticates to target hosts as a single dedicated identity. The
keypair is resolved with this priority:

  1. **env**      — ``$CIRCADIAND_SSH_KEY`` (+ ``$CIRCADIAND_SSH_PUBLIC_KEY`` or
                    ``<private>.pub``). Both files must exist; pointing the env at
                    a missing file is an error, never a trigger to generate.
  2. **file**     — ``/config/circadiand`` + ``.pub`` if already present.
  3. **generate** — a fresh ed25519 keypair written to ``/config/circadiand``.

The private key is used by the ``ssh`` method to connect; the public key is
served at ``/public-key`` so it can be installed onto target hosts.
"""

import logging
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .errors import ConfigError
from .methods.ssh import ENV_SSH_KEY
from .utils import get_env_str

ENV_SSH_PUBLIC_KEY = "CIRCADIAND_SSH_PUBLIC_KEY"
PUBLIC_KEY_SUFFIX = ".pub"

# Default on-disk location when no env override is given (file/generate modes).
DEFAULT_KEY_DIR = "/config"
DEFAULT_KEY_NAME = "circadiand"

KEY_COMMENT = "circadiand"
PRIVATE_KEY_MODE = 0o600
PUBLIC_KEY_MODE = 0o644

_LOGGER = logging.getLogger("circadiand")


def _generate_keypair(private_path: Path, public_path: Path) -> None:
    """Write a new OpenSSH ed25519 keypair to the given paths."""
    key = Ed25519PrivateKey.generate()
    private_bytes = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = key.public_key().public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    )

    private_path.parent.mkdir(parents=True, exist_ok=True)
    private_path.write_bytes(private_bytes)
    private_path.chmod(PRIVATE_KEY_MODE)
    public_path.write_bytes(public_bytes + f" {KEY_COMMENT}\n".encode())
    public_path.chmod(PUBLIC_KEY_MODE)
    _LOGGER.info("generated new SSH identity keypair at %s", private_path)


def resolve_keypair() -> tuple[Path, Path]:
    """Resolve (private_path, public_path), generating the pair if necessary."""
    env_private = get_env_str(ENV_SSH_KEY)
    if env_private:
        private_path = Path(env_private)
        public_path = Path(get_env_str(ENV_SSH_PUBLIC_KEY) or env_private + PUBLIC_KEY_SUFFIX)
        if not private_path.is_file():
            raise ConfigError(f"private key not found: {private_path}")
        if not public_path.is_file():
            raise ConfigError(f"public key not found: {public_path}")
        return private_path, public_path

    private_path = Path(DEFAULT_KEY_DIR) / DEFAULT_KEY_NAME
    public_path = private_path.with_name(private_path.name + PUBLIC_KEY_SUFFIX)
    if not (private_path.is_file() and public_path.is_file()):
        _generate_keypair(private_path, public_path)
    return private_path, public_path


def load_identity() -> tuple[str, str]:
    """Resolve the keypair and return (private_key_path, public_key_text)."""
    private_path, public_path = resolve_keypair()
    return str(private_path), public_path.read_text().strip()
