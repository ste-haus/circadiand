"""Resolve, generate, and validate the circadiand SSH identity keypair.

circadiand authenticates to target hosts as a single dedicated identity. The
keypair is resolved with this priority:

  1. **env**      — ``$CIRCADIAND_SSH_KEY`` (+ ``$CIRCADIAND_SSH_PUBLIC_KEY`` or
                    ``<private>.pub``). Both files must exist; pointing the env at
                    a missing file is an error, never a trigger to generate.
  2. **config**   — the ``identity`` section of the config file, if it names a
                    ``private_key`` / ``public_key`` path.
  3. **default**  — ``<config-dir>/circadiand`` + ``.pub`` (same directory as the
                    config file).

For cases 2 and 3, the files are used if present and generated if absent. The
private key is used by the ``ssh`` method to connect; the public key is served
at ``/public-key`` so it can be installed onto target hosts.
"""

import logging
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .config import Identity
from .errors import ConfigError
from .methods.ssh import ENV_SSH_KEY
from .utils import get_env_str

ENV_SSH_PUBLIC_KEY = "CIRCADIAND_SSH_PUBLIC_KEY"
PUBLIC_KEY_SUFFIX = ".pub"

# Default key basename when no env/config path is given (lives in the config dir).
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


def _public_for(private_path: Path) -> Path:
    return private_path.with_name(private_path.name + PUBLIC_KEY_SUFFIX)


def resolve_keypair(config_dir: Path, identity: Identity) -> tuple[Path, Path]:
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

    if identity.private_key:
        private_path = Path(identity.private_key)
    else:
        private_path = Path(config_dir) / DEFAULT_KEY_NAME
    public_path = Path(identity.public_key) if identity.public_key else _public_for(private_path)

    private_exists = private_path.is_file()
    public_exists = public_path.is_file()
    if private_exists and public_exists:
        return private_path, public_path
    if private_exists or public_exists:
        raise ConfigError(
            f"incomplete keypair: exactly one of {private_path} / {public_path} "
            f"exists — provide both or neither"
        )
    _generate_keypair(private_path, public_path)
    return private_path, public_path


def load_identity(config_dir: Path, identity: Identity) -> tuple[str, str]:
    """Resolve the keypair and return (private_key_path, public_key_text)."""
    private_path, public_path = resolve_keypair(config_dir, identity)
    return str(private_path), public_path.read_text().strip()
