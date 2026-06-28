"""Entry point wiring and env helpers."""

import circadiand.main as main_module
from circadiand.main import build_parser
from circadiand.utils import get_env_int, get_env_str

from .conftest import SAMPLE_CONFIG


def test_env_helpers(monkeypatch):
    monkeypatch.setenv("CIRCADIAND_TEST_STR", "value")
    monkeypatch.setenv("CIRCADIAND_TEST_INT", "42")
    monkeypatch.setenv("CIRCADIAND_TEST_BAD_INT", "notanint")

    assert get_env_str("CIRCADIAND_TEST_STR", "fallback") == "value"
    assert get_env_str("CIRCADIAND_MISSING", "fallback") == "fallback"
    assert get_env_int("CIRCADIAND_TEST_INT", 0) == 42
    assert get_env_int("CIRCADIAND_TEST_BAD_INT", 7) == 7
    assert get_env_int("CIRCADIAND_MISSING", 7) == 7


def test_parser_defaults(monkeypatch):
    monkeypatch.delenv("CIRCADIAND_PORT", raising=False)
    args = build_parser().parse_args([])
    assert args.port == main_module.DEFAULT_PORT
    assert args.host == main_module.DEFAULT_HOST


def test_main_loads_config_and_runs(monkeypatch):
    started = {}

    def fake_run(app, host, port):
        started["host"] = host
        started["port"] = port

    monkeypatch.setenv("CIRCADIAND_SSH_KEY", "/keys/id")  # sample ssh methods need a key
    monkeypatch.setattr(main_module.uvicorn, "run", fake_run)
    monkeypatch.setattr(
        "sys.argv", ["circadiand", "--config", str(SAMPLE_CONFIG), "--port", "9999"]
    )

    main_module.main()

    assert started["port"] == 9999
