# circadiand

A small REST service for powering hosts **on** (Wake-on-LAN, IPMI) and **off** (SSH). Power methods are generic and extensible — adding a new one is a single file under `circadiand/methods/`.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/list` | List configured hosts, their methods/actions, and resolved power methods. |
| GET | `/public-key` | Return the circadiand SSH public key as plaintext. |
| GET | `/{host}` | Return the host's latest liveliness status (see [Health checks](#health-checks)). |
| POST | `/{host}/{action}?method={method}` | Power a host `up` or `down`. |

`action` is `up` or `down`. The `method` query param is optional; when omitted it resolves in order: explicit `?method=` → host `power.{up,down}` → global `defaults.power.{up,down}`. If nothing resolves the request is a 400. Examples:

```bash
curl -X POST localhost:8000/nas/up                 # uses nas's power.up method
curl -X POST localhost:8000/nas/up?method=wol      # force a specific method
curl -X POST localhost:8000/workstation/down
```

Interactive API docs are served at `/docs` (Swagger UI) and `/redoc`; the raw schema is at `/openapi.json`. Swagger operations and tags are sorted alphabetically (`swagger_ui_parameters` in `create_api`) — keep new routes findable by leaning on that rather than declaration order.

## Methods

Most methods talk to the machine's primary address, so it's set once as the host-level `host` (see [Configuration](#configuration)) and inherited; a method only needs its own `host` to point somewhere else. `ipmi` is exactly that case: it targets the BMC, which has its own management NIC and IP separate from the OS, so it carries its own `host`.

| Type | Action | Driver | Config |
|------|--------|--------|--------|
| `wol` | up | [`wakeonlan`](https://pypi.org/project/wakeonlan/) | `mac` (`broadcast`, `port`, `count` optional) |
| `ipmi` | up | [`pyghmi`](https://pypi.org/project/pyghmi/) | `host` (the BMC), `username`, `password` |
| `ssh` | down | [`paramiko`](https://pypi.org/project/paramiko/) | inherits `host` (`username`, `port`, `key_path`, `shutdown_command` optional) |
| `ping` | check | [`icmplib`](https://pypi.org/project/icmplib/) | inherits `host` (`count`, `timeout`, `privileged` optional) |

> IPMI power-off is implemented but disabled (`SUPPORTS_OFF = False`) until needed.

The `ping` method only probes liveliness — it can't power a host up or down. It's meant to be named by a host's `health` config (see [Health checks](#health-checks)). ICMP uses raw sockets, so the process needs root or `CAP_NET_RAW`; the Docker image runs as root, so it works out of the box.

## Identity

circadiand acts as a single dedicated `circadiand` SSH identity. The private key is used by the `ssh` method to connect; the public key is served at `GET /public-key`. The keypair is resolved at startup with this priority:

1. **env** — `CIRCADIAND_SSH_KEY` (private) plus `CIRCADIAND_SSH_PUBLIC_KEY` (defaults to `${CIRCADIAND_SSH_KEY}.pub`). Both files must exist, or startup fails — pointing the env at a missing file never triggers generation.
2. **config** — an `identity` section in the config file naming `private_key` / `public_key` paths (see [`circadiand/config.sample.yaml`](circadiand/config.sample.yaml)).
3. **default** — `circadiand` and `circadiand.pub` in the same directory as the config file.

For cases 2 and 3, the files are used if present and a fresh ed25519 keypair is generated (private `0600`) if absent. So a bare deployment with a writable config directory bootstraps its own identity on first run; set the env vars, an `identity` section, or drop a keypair in place to supply your own.

To provision a target host, fetch the public key and append it to the host's `authorized_keys`:

```bash
curl -s http://circadiand:8000/public-key >> ~/.ssh/authorized_keys
```

`/public-key` is intentionally unauthenticated even when `CIRCADIAND_API_TOKEN` is set — a public key is meant to be distributed, and hosts typically fetch it while being provisioned. Host-key checking uses `AutoAddPolicy` (trusted-network assumption).

## Configuration

A YAML file injected into the container. If no file exists at `CIRCADIAND_CONFIG` on startup, circadiand writes a demo config (based on the bundled sample) to that path and loads it — so a fresh deployment comes up with an editable example rather than an error. See [`circadiand/config.sample.yaml`](circadiand/config.sample.yaml):

```yaml
defaults:
  power:                       # global fallback method per action
    up: wol
    down: ssh
hosts:
  nas:
    host: "192.168.1.10"       # the machine's address, shared by ssh/ping/...
    power:                     # per-host method per action (overrides defaults)
      up: ipmi
      down: ssh
    methods:
      - type: wol
        mac: "aa:bb:cc:dd:ee:ff"
      - type: ipmi
        host: "192.168.1.50"   # the BMC — a separate IP from host above
        username: "ADMIN"
        password: "changeme"
      - type: ssh              # no host -> inherits 192.168.1.10
        username: "circadiand"
```

Each host declares its address once as the top-level `host`; `ssh`, `ping`, and any other address-targeting method inherit it (set a method's own `host` only to override). `ipmi` is the exception — it targets the BMC's separate management IP, so it always carries its own `host`.

Invalid config (unknown method type, duplicate type on a host, a `power` entry naming a method the host doesn't define, a shared-host method with no `host` to use, etc.) fails fast at startup.

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

The config file is watched for changes (mtime polling every `CIRCADIAND_RELOAD_INTERVAL` seconds, default 120; set to `0` to disable). On change, the host/method/power/health config is re-parsed and swapped in atomically with no restart and no dropped requests. A malformed edit is logged and the previous config is kept, so a bad file never takes the service down. The SSH identity is resolved once at startup and is **not** affected by reloads.

## Health checks

A host can be monitored for liveliness by adding a `health` block. Set it on a host to monitor that host, or top-level to monitor every host that defines the named method. The value is either a bare method type (`health: ping`, default interval) or a mapping with a custom `interval` in seconds (default **10**). `type` names one of the host's configured methods that supports checking (currently `ping`). A host's own `health` takes precedence over the top-level one.

```yaml
health: ping             # shorthand: monitor every host with a ping method, every 10s
hosts:
  nas:
    host: "192.168.1.10"
    health:              # mapping form for a non-default interval
      type: ping
      interval: 5        # every 5s instead of the top-level 10
    methods:
      - type: ping       # inherits host: 192.168.1.10
      # ... power methods ...
```

A background monitor runs one worker thread per monitored host, probing on the host's interval and recording the latest result. Workers are reconciled on live reload — adding, removing, or retiming a `health` block takes effect without a restart. If a global `health` default names a method a particular host doesn't define (or can't check), that host is skipped with a warning rather than failing startup; a per-host `health` naming a missing/incapable method **does** fail fast at load.

`GET /{host}` returns the latest status plus a rolling `samples` history — the recent probes, oldest first, bounded to the **last hour or 100 samples**, whichever is smaller (100 covers a full hour at intervals ≥ 36s; faster intervals are capped by count). It's kept in memory only, so it resets on restart.

```bash
curl -s -w '\n%{http_code}\n' localhost:8000/nas
# {
#   "hostname":"nas","state":"alive","method":"ping","interval":5,
#   "checked_at":"2026-07-01T12:00:10+00:00","detail":null,
#   "samples":[
#     {"state":"dead","checked_at":"2026-07-01T12:00:05+00:00","detail":"nas is not responding to ping"},
#     {"state":"alive","checked_at":"2026-07-01T12:00:10+00:00","detail":null}
#   ]
# }
```

| State | HTTP | Meaning |
|-------|------|---------|
| `alive` | 200 | The last probe reached the host. |
| `dead` | 503 | The last probe ran and the host was unreachable (`detail` carries the message). |
| `unknown` | 503 | The probe itself couldn't run (e.g. name resolution / permission error) or hasn't run yet. |

A known host with **no** health configured returns `404`, as does an unknown host. Like `/list` and `/public-key`, `GET /{host}` is unauthenticated even when `CIRCADIAND_API_TOKEN` is set.

## Networking

**circadiand must run with host networking.** The `wol` method sends Wake-on-LAN magic packets to the LAN broadcast address (`255.255.255.255` or the subnet broadcast). Docker's default bridge network NATs egress and does not forward broadcast frames onto the physical LAN, so a magic packet sent from a bridged container never reaches the target NIC. Host networking puts the container directly on the host's L2 segment, where the broadcast propagates. IPMI and SSH are unicast and work fine under bridge networking — but since `wol` is the typical "power on" path, host networking is effectively required for the service to do its primary job.

- **Docker**: `--network host` (Compose: `network_mode: host`). With host networking, port publishing (`-p`/`ports:`) is ignored — the service is reachable directly on the host at `CIRCADIAND_PORT` (default `8000`). Note that Docker Desktop on macOS/Windows runs containers in a VM, so host networking attaches to the VM's network, not the physical LAN; deploy on Linux for WOL to reach real hardware.
- **Kubernetes**: set `hostNetwork: true` on the pod spec (and typically `dnsPolicy: ClusterFirstWithHostNet`). The pod then binds `CIRCADIAND_PORT` on the node and shares the node's broadcast domain.

```yaml
# Kubernetes pod spec excerpt
spec:
  hostNetwork: true
  dnsPolicy: ClusterFirstWithHostNet
  containers:
    - name: circadiand
      image: ghcr.io/ste-haus/circadiand:latest
```

## Running

Common tasks are in the [`Makefile`](Makefile) (`make help` to list them).

Locally:

```bash
make install
make run                      # writes ./config.yaml from the sample on first run; override with CONFIG=...
```

Docker:

```bash
make docker-build             # builds ghcr.io/ste-haus/circadiand:{version,latest}
mkdir -p config
docker run --rm --network host \
  -v "$PWD/config:/config" \
  ghcr.io/ste-haus/circadiand:latest
```

Host networking is required for Wake-on-LAN (see [Networking](#networking)); with `--network host`, port publishing is dropped and the service listens directly on `CIRCADIAND_PORT` (default `8000`).

`/config` holds `config.yaml` and the SSH identity. On first start, with a writable mount and an empty `/config`, circadiand writes a demo `config.yaml` from the sample and generates an SSH keypair into `/config/circadiand[.pub]` (see [Identity](#identity)).

The image is published to `ghcr.io/ste-haus/circadiand` by the GitHub Actions workflow on every push to `main` (after tests pass), tagged with the `VERSION` file contents and `latest`.

## Tests

```bash
make install
make test
```

All tests mock the WOL/IPMI/SSH drivers — no real network or host I/O.
