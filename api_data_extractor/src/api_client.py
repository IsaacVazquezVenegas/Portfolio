"""HTTP client shared by all connectors.

Responsibilities: authentication, headers, timeouts, retries with
exponential back-off, rate limiting, HTTP error handling, JSON parsing and
— crucially — recording every response (successful or not) in a
:class:`RawStore` so that raw data is always preserved, whatever the
connector does with it.

Secrets are read from environment variables, injected into requests only,
and replaced by ``***`` in everything that is recorded or logged.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests

from . import APP_NAME, __version__
from .logging_config import register_secret
from .models import Authentication, RequestSettings

logger = logging.getLogger(APP_NAME)

RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_RETRY_AFTER_SECONDS = 120
REDACTED = "***"


class ApiError(Exception):
    """Base class for API communication errors."""


class AuthenticationError(ApiError):
    pass


class ApiRequestError(ApiError):
    def __init__(self, message: str, *, status_code: Optional[int] = None, request_id: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.request_id = request_id


class InvalidResponseError(ApiRequestError):
    pass


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class RawRecord:
    """One HTTP exchange, preserved verbatim (minus secrets)."""

    request_id: str
    source_id: str
    purpose: str
    method: str
    url: str
    params: Dict[str, Any]
    retrieved_at: str
    attempts: int
    status_code: Optional[int]
    elapsed_seconds: Optional[float]
    content_type: Optional[str]
    body: Any = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "source_id": self.source_id,
            "purpose": self.purpose,
            "method": self.method,
            "url": self.url,
            "params": self.params,
            "retrieved_at": self.retrieved_at,
            "attempts": self.attempts,
            "status_code": self.status_code,
            "elapsed_seconds": self.elapsed_seconds,
            "content_type": self.content_type,
            "error": self.error,
            "body": self.body,
        }


@dataclass
class RawStore:
    """Append-only store of raw API exchanges. Records are never modified."""

    records: List[RawRecord] = field(default_factory=list)

    def next_id(self) -> str:
        return f"req_{len(self.records) + 1:05d}"

    def add(self, record: RawRecord) -> None:
        self.records.append(record)

    def failed(self) -> List[RawRecord]:
        return [r for r in self.records if r.error]


@dataclass
class ApiResponse:
    request_id: str
    url: str
    status_code: int
    data: Any
    headers: Dict[str, str]


class RateLimiter:
    def __init__(self, min_interval: float, clock: Callable[[], float] = time.monotonic, sleep=time.sleep):
        self.min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._last: Optional[float] = None

    def wait(self) -> None:
        if self._last is not None and self.min_interval > 0:
            remaining = self.min_interval - (self._clock() - self._last)
            if remaining > 0:
                self._sleep(remaining)
        self._last = self._clock()


class ApiClient:
    def __init__(
        self,
        source_id: str,
        settings: RequestSettings,
        auth: Authentication,
        raw_store: RawStore,
        *,
        session: Optional[requests.Session] = None,
        sleep: Callable[[float], None] = time.sleep,
        environ=None,
    ):
        self.source_id = source_id
        self.settings = settings
        self.auth = auth
        self.raw_store = raw_store
        self.session = session or requests.Session()
        self._sleep = sleep
        self.rate_limiter = RateLimiter(settings.min_interval_seconds, sleep=sleep)
        self._secret: Optional[str] = None
        if auth.method != "none":
            environ = os.environ if environ is None else environ
            secret = environ.get(auth.env_var or "")
            if not secret:
                raise AuthenticationError(
                    f"source '{source_id}': environment variable '{auth.env_var}' with the API credential is not set"
                )
            self._secret = secret
            register_secret(secret)

    # -- request preparation -------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/json", "User-Agent": f"{APP_NAME}/{__version__}"}
        headers.update(self.settings.headers)
        if self.auth.method == "api_key_header":
            headers[self.auth.param_name] = self._secret
        elif self.auth.method == "bearer_token":
            headers["Authorization"] = f"Bearer {self._secret}"
        return headers

    def _params(self, params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        params = dict(params or {})
        if self.auth.method == "api_key_query":
            params[self.auth.param_name] = self._secret
        return params

    def redact(self, text: str) -> str:
        if self._secret and text:
            return text.replace(self._secret, REDACTED)
        return text

    def _redact_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        out = {}
        for key, value in params.items():
            if self.auth.method == "api_key_query" and key == self.auth.param_name:
                out[key] = REDACTED
            else:
                out[key] = value
        return out

    def _redact_url(self, url: str) -> str:
        parsed = urlparse(url)
        if parsed.query and self.auth.method == "api_key_query":
            query = [(k, REDACTED if k == self.auth.param_name else v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)]
            url = urlunparse(parsed._replace(query=urlencode(query)))
        return self.redact(url)

    # -- execution -------------------------------------------------------------

    def get(self, url: str, params: Optional[Dict[str, Any]] = None, *, purpose: str = "data") -> ApiResponse:
        return self.request("GET", url, params=params, purpose=purpose)

    def post(self, url: str, json_body: Any = None, params: Optional[Dict[str, Any]] = None, *, purpose: str = "data") -> ApiResponse:
        return self.request("POST", url, params=params, json_body=json_body, purpose=purpose)

    def request(
        self,
        method: str,
        url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Any = None,
        purpose: str = "data",
    ) -> ApiResponse:
        if urlparse(url).scheme not in ("https", "http"):
            raise ApiRequestError(f"refusing to request non-HTTP URL: {url}")
        request_id = self.raw_store.next_id()
        sent_params = self._params(params)
        recorded_params = self._redact_params(dict(params or {}))
        attempts = 0
        last_error: Optional[str] = None
        response: Optional[requests.Response] = None
        started = time.monotonic()
        retrieved_at = utc_now_iso()

        while True:
            attempts += 1
            self.rate_limiter.wait()
            retrieved_at = utc_now_iso()
            started = time.monotonic()
            try:
                response = self.session.request(
                    method,
                    url,
                    params=sent_params or None,
                    json=json_body,
                    headers=self._headers(),
                    timeout=self.settings.timeout_seconds,
                )
                last_error = None
            except (requests.ConnectionError, requests.Timeout) as exc:
                response = None
                last_error = f"{type(exc).__name__}: {self.redact(str(exc))}"
            except requests.RequestException as exc:  # not retryable (invalid URL, etc.)
                response = None
                last_error = f"{type(exc).__name__}: {self.redact(str(exc))}"
                break

            retryable = response is None or response.status_code in RETRYABLE_STATUS
            if not retryable or attempts > self.settings.max_retries:
                break
            delay = self.settings.backoff_seconds * (2 ** (attempts - 1))
            if response is not None and response.headers.get("Retry-After", "").isdigit():
                delay = min(float(response.headers["Retry-After"]), MAX_RETRY_AFTER_SECONDS)
            reason = last_error or f"HTTP {response.status_code}"
            logger.warning("%s: %s - retrying in %.1fs (attempt %d/%d)", request_id, reason, delay, attempts, self.settings.max_retries + 1)
            self._sleep(delay)

        elapsed = round(time.monotonic() - started, 3)
        final_url = self._redact_url(response.url if response is not None and response.url else url)
        record = RawRecord(
            request_id=request_id,
            source_id=self.source_id,
            purpose=purpose,
            method=method,
            url=final_url,
            params=recorded_params,
            retrieved_at=retrieved_at,
            attempts=attempts,
            status_code=response.status_code if response is not None else None,
            elapsed_seconds=elapsed,
            content_type=response.headers.get("Content-Type") if response is not None else None,
        )

        if response is None:
            record.error = last_error or "request failed"
            self.raw_store.add(record)
            logger.error("%s: %s %s failed after %d attempt(s): %s", request_id, method, final_url, attempts, record.error)
            raise ApiRequestError(record.error, request_id=request_id)

        data: Any = None
        parse_error: Optional[str] = None
        try:
            data = response.json()
        except ValueError:
            data = self.redact(response.text)
            parse_error = "response is not valid JSON"
        record.body = data

        if response.status_code >= 400:
            record.error = f"HTTP {response.status_code}"
            if response.status_code in (401, 403):
                record.error += " (authentication or authorisation failure)"
            self.raw_store.add(record)
            logger.error("%s: %s %s returned %s", request_id, method, final_url, record.error)
            raise ApiRequestError(record.error, status_code=response.status_code, request_id=request_id)

        if parse_error:
            record.error = parse_error
            self.raw_store.add(record)
            logger.error("%s: %s %s: %s", request_id, method, final_url, parse_error)
            raise InvalidResponseError(parse_error, status_code=response.status_code, request_id=request_id)

        self.raw_store.add(record)
        logger.info("%s: %s %s -> HTTP %s (%.2fs)", request_id, method, final_url, response.status_code, elapsed)
        return ApiResponse(
            request_id=request_id,
            url=final_url,
            status_code=response.status_code,
            data=data,
            headers=dict(response.headers),
        )


def dump_raw_store(store: RawStore) -> str:
    return json.dumps([r.to_dict() for r in store.records], indent=2, ensure_ascii=False, default=str)
