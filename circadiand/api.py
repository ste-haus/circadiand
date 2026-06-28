"""FastAPI application: list/power/public-key routes with optional auth."""

from enum import Enum
from typing import Optional, Union

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from . import __version__
from .config import Config
from .errors import RequestError, UnsupportedAction
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

STATUS_OK = "ok"


class MethodInfo(BaseModel):
    type: str = Field(..., description="Method type, e.g. 'wol', 'ipmi', 'ssh'.")
    actions: list[str] = Field(..., description="Actions this method supports.")


class HostInfo(BaseModel):
    methods: list[MethodInfo]
    defaults: dict[str, str] = Field(
        default_factory=dict,
        description="Resolved default method type per action (host or global).",
    )


class ActionResult(BaseModel):
    hostname: str
    method: str
    action: str
    status: str = STATUS_OK
    detail: str


def _resolved_defaults(config: Config, hostname: str) -> dict[str, str]:
    host = config.hosts[hostname]
    resolved: dict[str, str] = {}
    for action in ACTIONS:
        method_type = host.defaults.get(action) or config.defaults.get(action)
        if method_type and method_type in host.methods:
            resolved[action] = method_type
    return resolved


def create_api(
    config: Union[Config, ConfigStore],
    api_token: Optional[str] = None,
    public_key: Optional[str] = None,
) -> FastAPI:
    app = FastAPI(title=APP_TITLE, version=__version__, description=APP_DESCRIPTION)
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
    async def _handle_request_error(_request: Request, exc: RequestError) -> JSONResponse:
        return JSONResponse(status_code=int(exc.status_code), content={"detail": str(exc)})

    error_responses = {
        status.HTTP_400_BAD_REQUEST: {"description": "Unsupported action or no default"},
        status.HTTP_404_NOT_FOUND: {"description": "Host or method not found"},
        status.HTTP_502_BAD_GATEWAY: {"description": "Power method failed on target"},
    }

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
                methods=methods, defaults=_resolved_defaults(active, name)
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
        detail = await run_in_threadpool(resolved.run, action.value)
        return ActionResult(
            hostname=hostname, method=resolved.TYPE, action=action.value, detail=detail
        )

    return app
