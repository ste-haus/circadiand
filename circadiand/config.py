"""Load and validate the YAML host/method configuration.

Shape::

    defaults:
      method:
        up: wol
        down: ssh
    health:                 # top-level global health default (optional)
      type: ping
      interval: 10
    hosts:
      nas:
        default:
          method:
            up: ipmi
            down: ssh
        health:             # per-host override, a sibling of methods (optional)
          type: ping
          interval: 5
        methods:
          - type: wol
            mac: "aa:bb:cc:dd:ee:ff"
          - type: ipmi
            host: "192.168.1.50"
            username: ADMIN
            password: secret
          - type: ping
            host: "192.168.1.50"

Parsing is strict and fails fast: unknown method types, duplicate types on a
host, a per-host default naming a method the host doesn't define, or a malformed
structure all raise :class:`ConfigError` at startup.
"""

import dataclasses
from importlib import resources
from pathlib import Path
from typing import Any, Optional

import yaml

from .errors import ConfigError, HostNotFound, MethodNotFound, NoDefaultMethod
from .methods import ACTION_CHECK, ACTIONS, METHOD_REGISTRY, Method

SAMPLE_CONFIG_FILENAME = "config.sample.yaml"

KEY_DEFAULTS = "defaults"
KEY_DEFAULT = "default"
KEY_METHOD = "method"
KEY_METHODS = "methods"
KEY_HOSTS = "hosts"
KEY_TYPE = "type"
KEY_IDENTITY = "identity"
KEY_PRIVATE_KEY = "private_key"
KEY_PUBLIC_KEY = "public_key"
KEY_HEALTH = "health"
KEY_INTERVAL = "interval"

DEFAULT_HEALTH_INTERVAL_SECONDS = 10


@dataclasses.dataclass
class Identity:
    """Optional SSH keypair locations from the config file. ``None`` means fall
    back to the default location (the config file's directory)."""

    private_key: Optional[str] = None
    public_key: Optional[str] = None


@dataclasses.dataclass
class Health:
    """Liveliness-check config: which method type to probe with, how often."""

    type: str
    interval: int = DEFAULT_HEALTH_INTERVAL_SECONDS


@dataclasses.dataclass
class Host:
    name: str
    methods: dict[str, Method]          # method type -> instance
    defaults: dict[str, str]            # action -> method type (subset of ACTIONS)
    health: Optional[Health] = None     # per-host health override


@dataclasses.dataclass
class Config:
    hosts: dict[str, Host]
    defaults: dict[str, str]            # global action -> method type fallback
    identity: Identity = dataclasses.field(default_factory=Identity)
    health: Optional[Health] = None     # global health fallback

    def get_host(self, hostname: str) -> Host:
        host = self.hosts.get(hostname)
        if host is None:
            raise HostNotFound(hostname)
        return host

    def resolve(self, hostname: str, action: str, requested: Optional[str] = None) -> Method:
        """Resolve the Method to run for an action.

        Order: explicit request -> host default -> global default. Raises
        NoDefaultMethod (400) if nothing resolves, MethodNotFound (404) if the
        resolved type isn't defined on the host.
        """
        host = self.get_host(hostname)
        if requested:
            method_type = requested
        else:
            method_type = host.defaults.get(action) or self.defaults.get(action)
            if not method_type:
                raise NoDefaultMethod(hostname, action)
        method = host.methods.get(method_type)
        if method is None:
            raise MethodNotFound(hostname, method_type)
        return method

    def resolve_health(self, hostname: str) -> Optional[tuple[Method, int]]:
        """Resolve the (method, interval) to health-check a host, or None.

        Order: per-host health -> global health. Returns None when no health is
        configured, or when a *global* health default names a method this host
        doesn't define or that can't check (per-host health is validated at load
        time, so this only skips inapplicable globals — the caller warns).
        """
        host = self.get_host(hostname)
        health = host.health or self.health
        if health is None:
            return None
        method = host.methods.get(health.type)
        if method is None or not method.supports(ACTION_CHECK):
            return None
        return method, health.interval


def _parse_method_defaults(block: Any, where: str) -> dict[str, str]:
    """Parse a ``default``/``defaults`` block into {action: method_type}."""
    if block is None:
        return {}
    if not isinstance(block, dict):
        raise ConfigError(f"{where} block must be a mapping")
    method_block = block.get(KEY_METHOD)
    if method_block is None:
        return {}
    if not isinstance(method_block, dict):
        raise ConfigError(f"{where} '{KEY_METHOD}' must be a mapping")
    result: dict[str, str] = {}
    for action, method_type in method_block.items():
        if action not in ACTIONS:
            raise ConfigError(
                f"{where} has unknown default action '{action}' "
                f"(expected one of {', '.join(ACTIONS)})"
            )
        result[action] = method_type
    return result


def _parse_health(health_block: Any, where: str) -> Optional[Health]:
    """Parse a ``health`` block (a sibling of ``methods`` per host, or top-level).

    Validates shape only (a non-empty ``type`` string and a positive integer
    ``interval``, defaulting to 10). Whether the ``type`` is a usable method is
    validated by the caller, which has the relevant method scope.
    """
    if health_block is None:
        return None
    if not isinstance(health_block, dict):
        raise ConfigError(f"{where} '{KEY_HEALTH}' must be a mapping")

    health_type = health_block.get(KEY_TYPE)
    if not health_type or not isinstance(health_type, str):
        raise ConfigError(f"{where} '{KEY_HEALTH}' is missing a string '{KEY_TYPE}'")

    interval = health_block.get(KEY_INTERVAL, DEFAULT_HEALTH_INTERVAL_SECONDS)
    if not isinstance(interval, int) or isinstance(interval, bool) or interval <= 0:
        raise ConfigError(
            f"{where} '{KEY_HEALTH}.{KEY_INTERVAL}' must be a positive integer"
        )

    return Health(type=health_type, interval=interval)


def _parse_identity(block: Any) -> Identity:
    if block is None:
        return Identity()
    if not isinstance(block, dict):
        raise ConfigError(f"'{KEY_IDENTITY}' must be a mapping")
    private = block.get(KEY_PRIVATE_KEY)
    public = block.get(KEY_PUBLIC_KEY)
    for key, value in ((KEY_PRIVATE_KEY, private), (KEY_PUBLIC_KEY, public)):
        if value is not None and not isinstance(value, str):
            raise ConfigError(f"'{KEY_IDENTITY}.{key}' must be a string path")
    return Identity(private_key=private, public_key=public)


def _parse_host(name: str, block: Any) -> Host:
    if not isinstance(block, dict):
        raise ConfigError(f"host '{name}' must be a mapping")

    methods_list = block.get(KEY_METHODS)
    if not isinstance(methods_list, list) or not methods_list:
        raise ConfigError(f"host '{name}' must define a non-empty '{KEY_METHODS}' list")

    methods: dict[str, Method] = {}
    for entry in methods_list:
        if not isinstance(entry, dict):
            raise ConfigError(f"host '{name}' has a method that is not a mapping")
        method_type = entry.get(KEY_TYPE)
        if not method_type:
            raise ConfigError(f"host '{name}' has a method missing its '{KEY_TYPE}'")
        method_cls = METHOD_REGISTRY.get(method_type)
        if method_cls is None:
            known = ", ".join(sorted(METHOD_REGISTRY)) or "none"
            raise ConfigError(
                f"host '{name}' uses unknown method type '{method_type}' "
                f"(known types: {known})"
            )
        if method_type in methods:
            raise ConfigError(
                f"host '{name}' defines method type '{method_type}' more than once"
            )
        params = {k: v for k, v in entry.items() if k != KEY_TYPE}
        methods[method_type] = method_cls(hostname=name, **params)

    defaults = _parse_method_defaults(block.get(KEY_DEFAULT), f"host '{name}'")
    for action, method_type in defaults.items():
        if method_type not in methods:
            raise ConfigError(
                f"host '{name}' default for '{action}' is '{method_type}' "
                f"but that method is not defined on the host"
            )

    # health is a sibling of methods, not a method-selection default.
    health = _parse_health(block.get(KEY_HEALTH), f"host '{name}'")
    if health is not None:
        method = methods.get(health.type)
        if method is None:
            raise ConfigError(
                f"host '{name}' health type is '{health.type}' "
                f"but that method is not defined on the host"
            )
        if not method.supports(ACTION_CHECK):
            raise ConfigError(
                f"host '{name}' health type '{health.type}' "
                f"does not support liveliness checks"
            )

    return Host(name=name, methods=methods, defaults=defaults, health=health)


def sample_config_text() -> str:
    """Return the bundled sample config (packaged alongside this module)."""
    return resources.files(__package__).joinpath(SAMPLE_CONFIG_FILENAME).read_text()


def ensure_config(path: str | Path) -> bool:
    """Create a demo config from the bundled sample if ``path`` doesn't exist.

    Returns True if a file was written, False if one was already present.
    """
    config_path = Path(path)
    if config_path.exists():
        return False
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(sample_config_text())
    return True


def load_config(path: str | Path) -> Config:
    """Read and validate the config file at ``path`` into a :class:`Config`."""
    config_path = Path(path)
    if not config_path.is_file():
        raise ConfigError(f"config file not found: {config_path}")

    try:
        raw = yaml.safe_load(config_path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"config file is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("config root must be a mapping")

    defaults_block = raw.get(KEY_DEFAULTS)
    global_defaults = _parse_method_defaults(defaults_block, KEY_DEFAULTS)
    for action, method_type in global_defaults.items():
        if method_type not in METHOD_REGISTRY:
            known = ", ".join(sorted(METHOD_REGISTRY)) or "none"
            raise ConfigError(
                f"{KEY_DEFAULTS} for '{action}' is unknown method type "
                f"'{method_type}' (known types: {known})"
            )

    # health is top-level (sibling of defaults/hosts), not a method default.
    global_health = _parse_health(raw.get(KEY_HEALTH), KEY_HEALTH)
    if global_health is not None:
        method_cls = METHOD_REGISTRY.get(global_health.type)
        if method_cls is None:
            known = ", ".join(sorted(METHOD_REGISTRY)) or "none"
            raise ConfigError(
                f"top-level {KEY_HEALTH} is unknown method type "
                f"'{global_health.type}' (known types: {known})"
            )
        if not method_cls.SUPPORTS_CHECK:
            raise ConfigError(
                f"top-level {KEY_HEALTH} type '{global_health.type}' "
                f"does not support liveliness checks"
            )

    hosts_block = raw.get(KEY_HOSTS)
    if not isinstance(hosts_block, dict) or not hosts_block:
        raise ConfigError(f"config must define a non-empty '{KEY_HOSTS}' mapping")

    identity = _parse_identity(raw.get(KEY_IDENTITY))

    hosts = {name: _parse_host(name, block) for name, block in hosts_block.items()}
    return Config(
        hosts=hosts,
        defaults=global_defaults,
        identity=identity,
        health=global_health,
    )
