import logging

import pytest
import requests

from conftest import FakeResponse, FakeSession, no_sleep, ok
from src.api_client import (
    ApiClient,
    ApiRequestError,
    AuthenticationError,
    InvalidResponseError,
    RateLimiter,
    RawStore,
)
from src.logging_config import get_logger, redact
from src.models import Authentication, RequestSettings

URL = "https://api.example.org/data"


def client(session, auth=None, environ=None, **settings):
    s = RequestSettings(min_interval_seconds=0, backoff_seconds=0.1, **settings)
    return ApiClient("test", s, auth or Authentication(), RawStore(), session=session, sleep=no_sleep,
                     environ=environ or {})


def test_successful_request_is_recorded():
    session = FakeSession().add("/data", ok({"value": [1, 2]}))
    c = client(session)
    resp = c.get(URL, {"a": 1})
    assert resp.data == {"value": [1, 2]}
    rec = c.raw_store.records[0]
    assert rec.request_id == resp.request_id == "req_00001"
    assert rec.status_code == 200 and rec.body == {"value": [1, 2]} and rec.error is None
    assert session.calls[0]["timeout"] == 60


def test_retries_on_server_error_then_succeeds():
    sleeps = []
    session = FakeSession().add("/data", FakeResponse(503, {"e": 1}), FakeResponse(429, {}, headers={"Retry-After": "3"}),
                                ok({"ok": True}))
    c = ApiClient("t", RequestSettings(min_interval_seconds=0, backoff_seconds=1), Authentication(), RawStore(),
                  session=session, sleep=sleeps.append, environ={})
    assert c.get(URL).data == {"ok": True}
    assert len(session.calls) == 3
    assert sleeps == [1.0, 3.0]  # exponential back-off, then Retry-After
    assert c.raw_store.records[0].attempts == 3


def test_retries_exhausted():
    session = FakeSession().add("/data", FakeResponse(500, {"e": 1}))
    c = client(session, max_retries=2)
    with pytest.raises(ApiRequestError) as exc:
        c.get(URL)
    assert exc.value.status_code == 500
    assert len(session.calls) == 3
    assert c.raw_store.records[0].error == "HTTP 500"


def test_client_error_is_not_retried_and_is_preserved():
    session = FakeSession().add("/data", FakeResponse(404, {"error": "not found"}))
    c = client(session)
    with pytest.raises(ApiRequestError):
        c.get(URL)
    assert len(session.calls) == 1
    assert c.raw_store.records[0].body == {"error": "not found"}


def test_connection_error_is_retried():
    session = FakeSession().add("/data", requests.ConnectionError("boom"), ok({"x": 1}))
    c = client(session)
    assert c.get(URL).data == {"x": 1}


def test_timeout_after_retries_is_recorded():
    session = FakeSession().add("/data", requests.Timeout("slow"))
    c = client(session, max_retries=1)
    with pytest.raises(ApiRequestError):
        c.get(URL)
    rec = c.raw_store.records[0]
    assert rec.status_code is None and "Timeout" in rec.error and rec.attempts == 2


def test_invalid_json_response():
    session = FakeSession().add("/data", FakeResponse(200, None, text="<html>maintenance</html>",
                                                      headers={"Content-Type": "text/html"}))
    c = client(session)
    with pytest.raises(InvalidResponseError):
        c.get(URL)
    assert c.raw_store.records[0].body == "<html>maintenance</html>"


def test_authentication_failure_message():
    session = FakeSession().add("/data", FakeResponse(401, {"error": "bad key"}))
    c = client(session, auth=Authentication(method="bearer_token", env_var="TOKEN"), environ={"TOKEN": "abcd1234"})
    with pytest.raises(ApiRequestError) as exc:
        c.get(URL)
    assert "authentication" in str(exc.value)


def test_missing_credential():
    with pytest.raises(AuthenticationError):
        client(FakeSession(), auth=Authentication(method="bearer_token", env_var="NOT_SET"), environ={})


def test_header_secret_sent_but_never_recorded(caplog):
    secret = "super-secret-token-123"
    session = FakeSession().add("/data", ok({"echo": secret}))
    c = client(session, auth=Authentication(method="api_key_header", env_var="K", param_name="X-API-Key"),
               environ={"K": secret})
    c.get(URL)
    assert session.calls[0]["headers"]["X-API-Key"] == secret
    logger = get_logger()
    logger.addHandler(caplog.handler)
    try:
        logger.info("token is %s", secret)
    finally:
        logger.removeHandler(caplog.handler)
    assert secret not in caplog.text and "***" in caplog.text
    assert redact(f"x{secret}x") == "x***x"


def test_query_secret_redacted_in_raw_record():
    secret = "query-secret-999"
    session = FakeSession().add("/data", ok({"v": 1}))
    c = client(session, auth=Authentication(method="api_key_query", env_var="K", param_name="api_key"),
               environ={"K": secret})
    c.get(URL, {"q": "x"})
    assert session.calls[0]["params"]["api_key"] == secret
    rec = c.raw_store.records[0]
    assert secret not in rec.url and secret not in str(rec.params)
    assert "api_key" not in rec.params  # only the caller's own parameters are recorded


def test_refuses_non_http_urls():
    c = client(FakeSession())
    with pytest.raises(ApiRequestError):
        c.get("file:///etc/passwd")


def test_rate_limiter_waits():
    t = [0.0]
    slept = []

    def sleep(s):
        slept.append(s)
        t[0] += s

    rl = RateLimiter(1.0, clock=lambda: t[0], sleep=sleep)
    rl.wait()
    t[0] += 0.25
    rl.wait()
    assert slept == [pytest.approx(0.75)]
