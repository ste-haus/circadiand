# circadiand

A small REST service for powering hosts **on** (Wake-on-LAN, IPMI) and **off** (SSH). Power methods are generic and extensible — adding a new one is a single file under `circadiand/methods/`.

## Endpoints

| Method | Path | Body | Description |
|--------|------|------|-------------|
| GET | `/list` | — | List configured hosts, their methods/actions, and resolved defaults. |
| POST | `/up` | `{"hostname": "...", "method": "..."?}` | Power a host on. |
| POST | `/down` | `{"hostname": "...", "method": "..."?}` | Power a host off. |

`method` is optional on `/up` and `/down`. When omitted it resolves in order: explicit request → host `default.method.{up,down}` → global `defaults.method.{up,down}`. If nothing resolves the request is a 400.

Interactive API docs are served at `/docs` (Swagger UI) and `/redoc`; the raw schema is at `/openapi.json`.

## Methods

| Type | Action | Driver | Required config |
|------|--------|--------|-----------------|
| `wol` | up | [`wakeonlan`](https://pypi.org/project/wakeonlan/) | `mac` (`broadcast`, `port` optional) |
| `ipmi` | up | [`pyghmi`](https://pypi.org/project/pyghmi/) | `host`, `username`, `password` |
| `ssh` | down | [`paramiko`](https://pypi.org/project/paramiko/) | `host` (`username`, `port`, `key_path`, `shutdown_command` optional) |

> IPMI power-off is implemented but disabled (`SUPPORTS_OFF = False`) until needed.

SSH uses key-based auth as the dedicated `circadiand` identity — inject the private key into the container and point `CIRCADIAND_SSH_KEY` (or a method's `key_path`) at it. All target hosts must trust this identity. Host-key checking uses `AutoAddPolicy` (trusted-network assumption).

## Configuration

A YAML file injected into the container. See [`config.sample.yaml`](config.sample.yaml):

```yaml
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
        host: "192.168.1.50"
        username: "ADMIN"
        password: "changeme"
      - type: ssh
        host: "192.168.1.10"
        username: "circadiand"
```

Invalid config (unknown method type, duplicate type on a host, a default naming a method the host doesn't define, etc.) fails fast at startup.

## Environment

| Variable | Default | Purpose |
|----------|---------|---------|
| `CIRCADIAND_CONFIG` | `/config/config.yaml` | Path to the config file. |
| `CIRCADIAND_HOST` | `0.0.0.0` | Bind host. |
| `CIRCADIAND_PORT` | `8000` | Bind port. |
| `CIRCADIAND_SSH_KEY` | — | Default private key path for the `ssh` method. |
| `CIRCADIAND_API_TOKEN` | — | If set, require `Authorization: Bearer <token>`. If unset, endpoints are open. |

## Running

Common tasks are in the [`Makefile`](Makefile) (`make help` to list them).

Locally:

```bash
make install
make run                      # uses config.sample.yaml; override with CONFIG=...
```

Docker:

```bash
make docker-build             # builds ghcr.io/ste-haus/circadiand:{version,latest}
docker run --rm -p 8000:8000 \
  -v "$PWD/config.yaml:/config/config.yaml:ro" \
  -v "$PWD/keys/circadiand:/keys/circadiand:ro" \
  ghcr.io/ste-haus/circadiand:latest
```

The image is published to `ghcr.io/ste-haus/circadiand` by the GitHub Actions workflow on every push to `main` (after tests pass), tagged with the `VERSION` file contents and `latest`.

## Tests

```bash
make install
make test
```

All tests mock the WOL/IPMI/SSH drivers — no real network or host I/O.
