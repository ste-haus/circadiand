"""FastAPI application: list/power/public-key routes with optional auth."""

import json
import logging
from enum import Enum
from importlib.resources import files
from typing import Optional, Union

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.openapi.docs import (
    get_redoc_html,
    get_swagger_ui_html,
    get_swagger_ui_oauth2_redirect_html,
)
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from . import __version__
from .config import Config
from .errors import (
    ExecutionError,
    HealthNotMonitored,
    HostNotFound,
    RequestError,
    UnsupportedAction,
)
from .health import HEALTH_ALIVE, HealthMonitor
from .methods import ACTION_DOWN, ACTION_UP, ACTIONS
from .reload import ConfigStore

APP_TITLE = "circadiand"
APP_DESCRIPTION = (
    "Power hosts up (Wake-on-LAN, IPMI) and down (SSH) via POST /{host}/{action}. "
    "The method is an optional query param; it falls back to the host then "
    "global default for the action."
)


class Action(str, Enum):
    up = ACTION_UP
    down = ACTION_DOWN

TAG_HOSTS = "hosts"
TAG_POWER = "power"
TAG_IDENTITY = "identity"
TAG_HEALTH = "health"

STATUS_OK = "ok"
STATUS_ERROR = "error"

_LOGGER = logging.getLogger("circadiand")

# Request log events, one per kind of externally initiated call.
LOG_EVENT_STATUS = "status"
LOG_EVENT_POWER = "power"
LOG_EVENT_REJECTED = "rejected"

METHOD_SOURCE_QUERY = "query"
METHOD_SOURCE_DEFAULT = "default"

PATH_PARAM_HOSTNAME = "hostname"

DOCS_URL = "/docs"
REDOC_URL = "/redoc"
FAVICON_URL = "/favicon.ico"
FAVICON_MEDIA_TYPE = "image/x-icon"
FAVICON_CACHE_CONTROL = "public, max-age=86400"
# Read once at import; shipped as package data (see setup.cfg).
FAVICON = files(__package__).joinpath("static/favicon.ico").read_bytes()
LOG_VALUE_SPECIAL_CHARS = ('"', "=")


def _format_log_value(value: object) -> str:
    text = str(value)
    needs_quoting = not text or any(
        char.isspace() or char in LOG_VALUE_SPECIAL_CHARS for char in text
    )
    return json.dumps(text) if needs_quoting else text


def _log_request(level: int, event: str, **fields: object) -> None:
    """Log a request as ``event key=value ...``, omitting fields that are None."""
    pairs = " ".join(
        f"{key}={_format_log_value(value)}"
        for key, value in fields.items()
        if value is not None
    )
    _LOGGER.log(level, "%s %s", event, pairs)


class MethodInfo(BaseModel):
    type: str = Field(..., description="Method type, e.g. 'wol', 'ipmi', 'ssh'.")
    actions: list[str] = Field(..., description="Actions this method supports.")


class HostInfo(BaseModel):
    methods: list[MethodInfo]
    power: dict[str, str] = Field(
        default_factory=dict,
        description="Resolved power method type per action (host or global).",
    )


class ActionResult(BaseModel):
    hostname: str
    method: str
    action: str
    status: str = STATUS_OK
    detail: str


class HealthSampleInfo(BaseModel):
    state: str = Field(..., description="Liveliness state at this probe.")
    checked_at: str = Field(..., description="UTC ISO-8601 timestamp of the probe.")
    detail: Optional[str] = Field(
        None, description="Down or error message when not alive."
    )


class HostHealth(BaseModel):
    hostname: str
    state: str = Field(..., description="Liveliness state: alive, dead, or unknown.")
    method: str = Field(..., description="Method type used to probe the host.")
    interval: int = Field(..., description="Probe interval in seconds.")
    checked_at: Optional[str] = Field(
        None, description="UTC ISO-8601 timestamp of the last probe, if any."
    )
    detail: Optional[str] = Field(
        None, description="Down or error message when the host is not alive."
    )
    samples: list[HealthSampleInfo] = Field(
        default_factory=list,
        description="Recent probes, oldest first — up to the last hour or 100 samples.",
    )


def _resolved_power(config: Config, hostname: str) -> dict[str, str]:
    host = config.hosts[hostname]
    resolved: dict[str, str] = {}
    for action in ACTIONS:
        method_type = host.power.get(action) or config.power.get(action)
        if method_type and method_type in host.methods:
            resolved[action] = method_type
    return resolved


def create_api(
    config: Union[Config, ConfigStore],
    api_token: Optional[str] = None,
    public_key: Optional[str] = None,
    health_monitor: Optional[HealthMonitor] = None,
) -> FastAPI:
    # Sort operations and tags alphabetically in the Swagger UI so the endpoint
    # list is stable and easy to scan (routes are declared in match-priority
    # order, which isn't alphabetical).
    #
    # FastAPI's built-in /docs and /redoc pages hardcode FastAPI's own favicon, so
    # they're disabled here and re-declared below with ours.
    app = FastAPI(
        title=APP_TITLE,
        version=__version__,
        description=APP_DESCRIPTION,
        swagger_ui_parameters={"operationsSorter": "alpha", "tagsSorter": "alpha"},
        docs_url=None,
        redoc_url=None,
    )
    bearer_scheme = HTTPBearer(auto_error=False)

    # Accept a live ConfigStore (reloadable) or a fixed Config. Handlers always
    # read the current config through this so live reloads take effect.
    store = config if isinstance(config, ConfigStore) else None

    def current() -> Config:
        return store.config if store is not None else config

    async def require_auth(
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    ) -> None:
        if not api_token:
            return
        if credentials is None or credentials.credentials != api_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid or missing bearer token",
            )

    @app.exception_handler(RequestError)
    async def _handle_request_error(request: Request, exc: RequestError) -> JSONResponse:
        # Power failures on the target are logged by the power handler with the
        # full action context.
        if not isinstance(exc, ExecutionError):
            _log_request(
                logging.WARNING,
                LOG_EVENT_REJECTED,
                host=request.path_params.get(PATH_PARAM_HOSTNAME),
                request=f"{request.method} {request.url.path}",
                status=int(exc.status_code),
                detail=str(exc),
            )
        return JSONResponse(status_code=int(exc.status_code), content={"detail": str(exc)})

    error_responses = {
        status.HTTP_400_BAD_REQUEST: {"description": "Unsupported action or no default"},
        status.HTTP_404_NOT_FOUND: {"description": "Host or method not found"},
        status.HTTP_502_BAD_GATEWAY: {"description": "Power method failed on target"},
    }

    def _root_path(request: Request) -> str:
        # Honour a proxy prefix the same way FastAPI's built-in docs routes do.
        return request.scope.get("root_path", "").rstrip("/")

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        # Send the bare root to the interactive API docs.
        return RedirectResponse(url=DOCS_URL)

    @app.get(FAVICON_URL, include_in_schema=False)
    def favicon() -> Response:
        # Unauthenticated, like the docs pages that reference it. Declared ahead
        # of GET /{hostname} so it isn't treated as an unknown host.
        return Response(
            content=FAVICON,
            media_type=FAVICON_MEDIA_TYPE,
            headers={"Cache-Control": FAVICON_CACHE_CONTROL},
        )

    @app.get(DOCS_URL, include_in_schema=False)
    def swagger_ui(request: Request) -> HTMLResponse:
        root_path = _root_path(request)
        oauth2_redirect_url = app.swagger_ui_oauth2_redirect_url
        return get_swagger_ui_html(
            openapi_url=root_path + app.openapi_url,
            title=f"{app.title} - Swagger UI",
            oauth2_redirect_url=(
                root_path + oauth2_redirect_url if oauth2_redirect_url else None
            ),
            init_oauth=app.swagger_ui_init_oauth,
            swagger_favicon_url=root_path + FAVICON_URL,
            swagger_ui_parameters=app.swagger_ui_parameters,
        )

    if app.swagger_ui_oauth2_redirect_url:

        @app.get(app.swagger_ui_oauth2_redirect_url, include_in_schema=False)
        def swagger_ui_oauth2_redirect() -> HTMLResponse:
            return get_swagger_ui_oauth2_redirect_html()

    @app.get(REDOC_URL, include_in_schema=False)
    def redoc(request: Request) -> HTMLResponse:
        root_path = _root_path(request)
        return get_redoc_html(
            openapi_url=root_path + app.openapi_url,
            title=f"{app.title} - ReDoc",
            redoc_favicon_url=root_path + FAVICON_URL,
        )

    @app.get(
        "/list",
        response_model=dict[str, HostInfo],
        tags=[TAG_HOSTS],
        summary="List configured hosts and their methods",
    )
    def list_hosts() -> dict[str, HostInfo]:
        active = current()
        result: dict[str, HostInfo] = {}
        for name, host in active.hosts.items():
            methods = [
                MethodInfo(
                    type=method.TYPE,
                    actions=[a for a in ACTIONS if method.supports(a)],
                )
                for method in host.methods.values()
            ]
            result[name] = HostInfo(
                methods=methods, power=_resolved_power(active, name)
            )
        return result

    @app.get(
        "/public-key",
        response_class=PlainTextResponse,
        tags=[TAG_IDENTITY],
        summary="Get the circadiand SSH public key",
        responses={
            status.HTTP_200_OK: {
                "content": {"text/plain": {}},
                "description": "The public key, suitable for an authorized_keys entry.",
            },
            status.HTTP_404_NOT_FOUND: {"description": "No public key configured"},
        },
    )
    def get_public_key() -> str:
        # Intentionally unauthenticated: a public key is meant to be distributed,
        # and hosts typically fetch it while being provisioned (before they trust
        # the identity).
        if not public_key:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="no public key configured",
            )
        return public_key

    @app.post(
        "/{hostname}/{action}",
        response_model=ActionResult,
        tags=[TAG_POWER],
        summary="Power a host up or down",
        responses=error_responses,
        dependencies=[Depends(require_auth)],
    )
    async def power(
        hostname: str,
        action: Action,
        method: Optional[str] = Query(
            None,
            description="Method type to use. Optional — falls back to the host "
            "then global default for the action.",
        ),
    ) -> ActionResult:
        resolved = current().resolve(hostname, action.value, method)
        if not resolved.supports(action.value):
            raise UnsupportedAction(resolved.TYPE, action.value)

        log_fields = {
            "host": hostname,
            "action": action.value,
            "method": resolved.TYPE,
            "method_source": METHOD_SOURCE_QUERY if method else METHOD_SOURCE_DEFAULT,
        }
        try:
            detail = await run_in_threadpool(resolved.run, action.value)
        except ExecutionError as exc:
            _log_request(
                logging.WARNING,
                LOG_EVENT_POWER,
                **log_fields,
                status=STATUS_ERROR,
                detail=exc.detail,
            )
            raise

        _log_request(
            logging.INFO, LOG_EVENT_POWER, **log_fields, status=STATUS_OK, detail=detail
        )
        return ActionResult(
            hostname=hostname, method=resolved.TYPE, action=action.value, detail=detail
        )

    # Declared last so the literal GET routes (/list, /public-key, /favicon.ico,
    # /docs, /redoc) and FastAPI's own /openapi.json win the match for a bare
    # depth-1 GET path.
    @app.get(
        "/{hostname}",
        response_model=HostHealth,
        tags=[TAG_HEALTH],
        summary="Get a host's latest liveliness status",
        responses={
            status.HTTP_404_NOT_FOUND: {
                "description": "Host not found or no health check configured"
            },
            status.HTTP_503_SERVICE_UNAVAILABLE: {
                "description": "Host is down (dead) or its state can't be confirmed"
            },
        },
    )
    def health(hostname: str, response: Response) -> HostHealth:
        # Intentionally unauthenticated: read-only liveliness metadata, the same
        # class as /list.
        active = current()
        if hostname not in active.hosts:
            raise HostNotFound(hostname)
        result = health_monitor.get(hostname) if health_monitor is not None else None
        if result is None:
            raise HealthNotMonitored(hostname)
        response.status_code = (
            status.HTTP_200_OK
            if result.state == HEALTH_ALIVE
            else status.HTTP_503_SERVICE_UNAVAILABLE
        )

        _log_request(
            logging.INFO,
            LOG_EVENT_STATUS,
            host=hostname,
            state=result.state,
            method=result.method,
            interval=result.interval,
            checked_at=result.checked_at,
            detail=result.detail,
        )
        return HostHealth(
            hostname=hostname,
            state=result.state,
            method=result.method,
            interval=result.interval,
            checked_at=result.checked_at,
            detail=result.detail,
            samples=[
                HealthSampleInfo(
                    state=s.state, checked_at=s.checked_at, detail=s.detail
                )
                for s in result.samples
            ],
        )

    return app
