# circadiand

A small REST service for powering hosts **on** (Wake-on-LAN, IPMI) and **off** (SSH). Power methods are generic and extensible — adding a new one is a single file under `circadiand/methods/`.

## Endpoints

| Method | Path | Body | Description |
|--------|------|------|-------------|
| GET | `/list` | — | List configured hosts, their methods/actions, and resolved defaults. |
| GET | `/public-key` | — | Return the circadiand SSH public key as plaintext. |
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

## Identity

circadiand acts as a single dedicated `circadiand` SSH identity. The private key is used by the `ssh` method to connect; the public key is served at `GET /public-key`. The keypair is resolved at startup with this priority:

1. **env** — `CIRCADIAND_SSH_KEY` (private) plus `CIRCADIAND_SSH_PUBLIC_KEY` (defaults to `${CIRCADIAND_SSH_KEY}.pub`). Both files must exist, or startup fails — pointing the env at a missing file never triggers generation.
2. **config** — an `identity` section in the config file naming `private_key` / `public_key` paths (see [`config.sample.yaml`](config.sample.yaml)).
3. **default** — `circadiand` and `circadiand.pub` in the same directory as the config file.

For cases 2 and 3, the files are used if present and a fresh ed25519 keypair is generated (private `0600`) if absent. So a bare deployment with a writable config directory bootstraps its own identity on first run; set the env vars, an `identity` section, or drop a keypair in place to supply your own.

To provision a target host, fetch the public key and append it to the host's `authorized_keys`:

```bash
curl -s http://circadiand:8000/public-key >> ~/.ssh/authorized_keys
```

`/public-key` is intentionally unauthenticated even when `CIRCADIAND_API_TOKEN` is set — a public key is meant to be distributed, and hosts typically fetch it while being provisioned. Host-key checking uses `AutoAddPolicy` (trusted-network assumption).

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
| `CIRCADIAND_SSH_KEY` | — | Private key path override for the `circadiand` identity. If set, the file must exist. |
| `CIRCADIAND_SSH_PUBLIC_KEY` | `${CIRCADIAND_SSH_KEY}.pub` | Public key path override (only used when `CIRCADIAND_SSH_KEY` is set). |
| `CIRCADIAND_API_TOKEN` | — | If set, require `Authorization: Bearer <token>`. If unset, endpoints are open. |
| `CIRCADIAND_RELOAD_INTERVAL` | `120` | Seconds between config-file change checks for live reload. Set to `0` to disable. |

## Live reload

The config file is watched for changes (mtime polling every `CIRCADIAND_RELOAD_INTERVAL` seconds, default 120; set to `0` to disable). On change, the host/method/defaults config is re-parsed and swapped in atomically with no restart and no dropped requests. A malformed edit is logged and the previous config is kept, so a bad file never takes the service down. The SSH identity is resolved once at startup and is **not** affected by reloads.

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
mkdir -p config && cp config.sample.yaml config/config.yaml
docker run --rm -p 8000:8000 \
  -v "$PWD/config:/config" \
  ghcr.io/ste-haus/circadiand:latest
```

`/config` holds `config.yaml` and the SSH identity. With a writable mount and no keypair present, circadiand generates one into `/config/circadiand[.pub]` on first start (see [Identity](#identity)).

The image is published to `ghcr.io/ste-haus/circadiand` by the GitHub Actions workflow on every push to `main` (after tests pass), tagged with the `VERSION` file contents and `latest`.

## Tests

```bash
make install
make test
```

All tests mock the WOL/IPMI/SSH drivers — no real network or host I/O.
