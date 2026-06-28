"""Entry point: parse args/env, load config, run uvicorn.

Configuration precedence: CLI flag > environment variable > built-in default.
"""

import argparse
import logging

import uvicorn

from . import __version__
from .api import create_api
from .config import load_config
from .utils import get_env_int, get_env_str

ENV_CONFIG = "CIRCADIAND_CONFIG"
ENV_HOST = "CIRCADIAND_HOST"
ENV_PORT = "CIRCADIAND_PORT"
ENV_API_TOKEN = "CIRCADIAND_API_TOKEN"

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

    api_token = get_env_str(ENV_API_TOKEN)
    if api_token:
        _LOGGER.info("bearer-token auth enabled")
    else:
        _LOGGER.warning("no %s set — endpoints are unauthenticated", ENV_API_TOKEN)

    app = create_api(config, api_token=api_token)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
