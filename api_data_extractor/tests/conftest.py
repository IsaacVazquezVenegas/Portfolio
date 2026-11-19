"""Shared fixtures: an offline fake HTTP session and configuration builders."""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union
from urllib.parse import urlencode

import pytest
import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MOCKS = ROOT / "tests" / "mock_responses"


def mock(name: str) -> Any:
    return json.loads((MOCKS / name).read_text(encoding="utf-8"))


class FakeResponse:
    def __init__(self, status_code: int = 200, body: Any = None, *, text: Optional[str] = None,
                 headers: Optional[Dict[str, str]] = None, url: str = ""):
        self.status_code = status_code
        self._body = body
        self.text = text if text is not None else json.dumps(body)
        self.headers = headers or {"Content-Type": "application/json"}
        self.url = url

    def json(self):
        if self._body is None and self.text:
            return json.loads(self.text)
        if self._body is None:
            raise ValueError("no JSON")
        return self._body


Route = Dict[str, Any]


class FakeSession:
    """Routes requests to canned responses.

    ``add(match, *responses)``: ``match`` is a substring of the URL or a
    callable ``(method, url, params) -> bool``. Responses are consumed in
    order; the last one is repeated. A response may be an exception instance
    (raised) or a :class:`FakeResponse`.
    """

    def __init__(self):
        self.routes: List[Route] = []
        self.calls: List[Dict[str, Any]] = []

    def add(self, match: Union[str, Callable], *responses):
        self.routes.append({"match": match, "responses": list(responses)})
        return self

    def request(self, method, url, params=None, json=None, headers=None, timeout=None):
        self.calls.append({"method": method, "url": url, "params": params, "headers": headers, "timeout": timeout})
        for route in self.routes:
            m = route["match"]
            ok = m(method, url, params or {}) if callable(m) else m in url
            if ok:
                resp = route["responses"][0] if len(route["responses"]) == 1 else route["responses"].pop(0)
                if isinstance(resp, Exception):
                    raise resp
                full = url + ("?" + urlencode(params or {}, doseq=True) if params else "")
                return FakeResponse(resp.status_code, resp._body, text=resp.text, headers=resp.headers, url=full)
        return FakeResponse(404, {"error": "no fake route"}, url=url)


def ok(body: Any) -> FakeResponse:
    return FakeResponse(200, body)


@pytest.fixture
def fake_session() -> FakeSession:
    return FakeSession()


def base_config() -> Dict[str, Any]:
    return {
        "schema_version": "1.0",
        "project": {
            "name": "TEST_PROJECT",
            "description": "Test configuration",
            "version": "1.0.0",
            "created": "2026-11-19",
        },
        "sources": [
            {
                "id": "who_gho",
                "provider": "World Health Organization",
                "api_name": "GHO OData API",
                "api_type": "odata",
                "connector": "who_gho",
                "base_url": "https://ghoapi.azureedge.net/api",
                "documentation_url": "https://www.who.int/data/gho/info/gho-odata-api",
                "request": {"min_interval_seconds": 0, "backoff_seconds": 0},
            }
        ],
        "extractions": [
            {
                "id": "mpower",
                "source": "who_gho",
                "countries": ["FRA", "DEU"],
                "start_year": 2007,
                "end_year": 2008,
                "indicators": [
                    {"code": "R_Group", "name": "Raising taxes on tobacco", "category": "R", "measurement_scale": "ordinal"}
                ],
            }
        ],
        "processing": {"wide_panel": True},
        "output": {"directory": "output", "folder_name": "{project}", "formats": ["csv", "xlsx", "json"]},
    }


@pytest.fixture
def config_dict() -> Dict[str, Any]:
    return copy.deepcopy(base_config())


def write_config(tmp_path: Path, data: Dict[str, Any], name: str = "extraction_config.yaml") -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def no_sleep(_seconds: float) -> None:
    return None


def pytest_addoption(parser):
    parser.addoption("--live", action="store_true", default=False, help="run tests against real public APIs")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--live") or os.environ.get("RUN_LIVE_TESTS") == "1":
        return
    skip = pytest.mark.skip(reason="live API test: use --live or RUN_LIVE_TESTS=1")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)
