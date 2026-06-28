"""Entry point: parse args/env, load config, run uvicorn.

Configuration precedence: CLI flag > environment variable > built-in default.
"""

import argparse
import logging
import os
from pathlib import Path

import uvicorn

from . import __version__
from .api import create_api
from .config import load_config
from .keys import load_identity
from .methods.ssh import ENV_SSH_KEY
from .reload import DEFAULT_RELOAD_INTERVAL_SECONDS, ConfigStore, start_config_watcher
from .utils import get_env_int, get_env_str

ENV_CONFIG = "CIRCADIAND_CONFIG"
ENV_HOST = "CIRCADIAND_HOST"
ENV_PORT = "CIRCADIAND_PORT"
ENV_API_TOKEN = "CIRCADIAND_API_TOKEN"
ENV_RELOAD_INTERVAL = "CIRCADIAND_RELOAD_INTERVAL"

DEFAULT_CONFIG_PATH = "/config/config.yaml"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8000

_LOGGER = logging.getLogger("circadiand")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="circadiand host power control service")
    parser.add_argument(
        "--config",
        default=get_env_str(ENV_CONFIG, DEFAULT_CONFIG_PATH),
        help=f"Path to the YAML config (env: {ENV_CONFIG}, default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--host",
        default=get_env_str(ENV_HOST, DEFAULT_HOST),
        help=f"Bind host (env: {ENV_HOST}, default: {DEFAULT_HOST})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=get_env_int(ENV_PORT, DEFAULT_PORT),
        help=f"Bind port (env: {ENV_PORT}, default: {DEFAULT_PORT})",
    )
    return parser


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    args = build_parser().parse_args()

    _LOGGER.info("circadiand %s starting", __version__)
    _LOGGER.info("loading config from %s", args.config)
    config = load_config(args.config)
    _LOGGER.info("loaded %d host(s): %s", len(config.hosts), ", ".join(config.hosts))

    # Resolve the identity (env > config identity > default in the config dir;
    # generated if absent). Export the resolved private key path so ssh methods
    # use the same key when no explicit per-method key_path was given.
    config_dir = Path(args.config).resolve().parent
    private_key_path, public_key = load_identity(config_dir, config.identity)
    os.environ[ENV_SSH_KEY] = private_key_path
    _LOGGER.info("using SSH identity private key at %s", private_key_path)

    api_token = get_env_str(ENV_API_TOKEN)
    if api_token:
        _LOGGER.info("bearer-token auth enabled")
    else:
        _LOGGER.warning("no %s set — endpoints are unauthenticated", ENV_API_TOKEN)

    store = ConfigStore(args.config, config)
    reload_interval = get_env_int(ENV_RELOAD_INTERVAL, DEFAULT_RELOAD_INTERVAL_SECONDS)
    if reload_interval > 0:
        start_config_watcher(store, reload_interval)
        _LOGGER.info("watching %s for changes every %ds", args.config, reload_interval)
    else:
        _LOGGER.info("config live-reload disabled (%s=0)", ENV_RELOAD_INTERVAL)

    app = create_api(store, api_token=api_token, public_key=public_key)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
