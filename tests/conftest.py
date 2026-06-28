"""Shared test fixtures and a driver-free fake Method."""

from pathlib import Path
from typing import Optional

import pytest
from fastapi.testclient import TestClient

from circadiand.api import create_api
from circadiand.config import Config, Host
from circadiand.methods.base import ACTION_DOWN, ACTION_UP, Method

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_CONFIG = REPO_ROOT / "circadiand" / "config.sample.yaml"


class FakeMethod(Method):
    """A Method with no real I/O; records calls and can be made to raise."""

    def __init__(
        self,
        method_type: str,
        hostname: str = "host",
        up: bool = False,
        down: bool = False,
        result: Optional[str] = None,
        raises: Optional[Exception] = None,
    ):
        super().__init__(hostname)
        self.TYPE = method_type
        self.SUPPORTS_UP = up
        self.SUPPORTS_DOWN = down
        self._result = result if result is not None else f"{method_type} ran"
        self._raises = raises
        self.calls: list[str] = []

    def power_up(self) -> str:
        return self._act(ACTION_UP)

    def power_down(self) -> str:
        return self._act(ACTION_DOWN)

    def _act(self, action: str) -> str:
        self.calls.append(action)
        if self._raises is not None:
            raise self._raises
        return self._result


def make_host(name: str, methods: list[Method], defaults: Optional[dict] = None) -> Host:
    return Host(
        name=name,
        methods={m.TYPE: m for m in methods},
        defaults=defaults or {},
    )


@pytest.fixture
def config() -> Config:
    nas = make_host(
        "nas",
        [
            FakeMethod("wol", "nas", up=True, result="wol sent"),
            FakeMethod("ipmi", "nas", up=True, result="ipmi on"),
            FakeMethod("ssh", "nas", down=True, result="ssh shutdown"),
        ],
        defaults={ACTION_UP: "ipmi", ACTION_DOWN: "ssh"},
    )
    workstation = make_host(
        "workstation",
        [
            FakeMethod("wol", "workstation", up=True),
            FakeMethod("ssh", "workstation", down=True),
        ],
    )
    return Config(
        hosts={"nas": nas, "workstation": workstation},
        defaults={ACTION_UP: "wol", ACTION_DOWN: "ssh"},
    )


FAKE_PUBLIC_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITESTKEY circadiand"


@pytest.fixture
def client(config: Config) -> TestClient:
    return TestClient(create_api(config, public_key=FAKE_PUBLIC_KEY))
