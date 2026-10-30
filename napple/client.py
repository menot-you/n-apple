"""The App Store Connect HTTP client: one JWT, bounded GET retries, typed errors."""

from __future__ import annotations

import math
import time
from typing import Any
from urllib.parse import unquote, urlsplit

import httpx2

from . import auth
from .config import Config
from .errors import (
    AgreementError,
    AppleError,
    AuthError,
    ConflictError,
    NotFoundError,
    ProtocolError,
    RateLimitError,
)

AGREEMENT = "FORBIDDEN.REQUIRED_AGREEMENTS_MISSING_OR_EXPIRED"
RETRIES = 3
MAX_WAIT = 10.0
RENEW_MARGIN = 300.0  # Mint a new token 5 minutes before the old one expires.


class Client:
    """A synchronous client for the paths under `config.api_root`."""

    def __init__(self, config: Config, *, timeout: float = 10.0) -> None:
        self._config = config
        self._timeout = timeout
        self._http = httpx2.Client(timeout=timeout, follow_redirects=False)
        self._token: str | None = None
        self._expires = 0.0

    def get(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        return self._call("GET", self._url(path), params=params)

    def get_all(
        self, path: str, params: dict[str, str] | None = None, *, limit: int = 200
    ) -> list[dict[str, Any]]:
        """Follow `links.next`, join each page's `data`, and stop at `limit` items."""
        page = self.get(path, params)
        items: list[dict[str, Any]] = []
        while True:
            data = page.get("data")
            if not isinstance(data, list):
                raise ProtocolError(f"GET {path}: a page has no data list")
            items.extend(data)
            if len(items) >= limit:
                return items[:limit]
            links = page.get("links")
            next_url = links.get("next") if isinstance(links, dict) else None
            if next_url is None:
                return items
            page = self._call("GET", self._next(next_url))

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._call("POST", self._url(path), json=body)

    def patch(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._call("PATCH", self._url(path), json=body)

    def put_part(
        self, url: str, method: str, headers: dict[str, str], data: bytes
    ) -> None:
        """Send one upload part to an Apple-supplied URL. The URL is not the API, so send no JWT."""
        self._send(method, url, signed=False, headers=headers, content=data)

    def close(self) -> None:
        self._http.close()

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            raise ProtocolError(f"the API path must start with '/': {path!r}")
        return self._config.api_root + path

    def _next(self, url: object) -> str:
        prefix = self._config.api_root + "/v1/"
        if not isinstance(url, str) or not url.startswith(prefix):
            raise ProtocolError(f"refused a links.next outside {prefix}: {url!r}")
        segments = unquote(urlsplit(url).path).split("/")
        if "." in segments or ".." in segments:
            raise ProtocolError(f"refused a links.next with a dot segment: {url!r}")
        return url

    def _bearer(self) -> str:
        now = time.time()
        if self._token is None or now >= self._expires - RENEW_MARGIN:
            self._token = auth.token(self._config, now=now)
            self._expires = now + auth.LIFETIME
        return self._token

    def _call(self, method: str, url: str, **options: Any) -> dict[str, Any]:
        response = self._send(method, url, **options)
        if response.status_code == 204:
            return {}
        try:
            body = response.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise ProtocolError(
                f"{_where(method, url)}: the HTTP {response.status_code} body is not a JSON object",
                status=response.status_code,
            )
        return body

    def _send(
        self,
        method: str,
        url: str,
        *,
        signed: bool = True,
        headers: dict[str, str] | None = None,
        **options: Any,
    ) -> httpx2.Response:
        retry = signed and method == "GET"
        for attempt in range(RETRIES + 1):
            sent = dict(headers or {})
            if signed:
                sent["Authorization"] = f"Bearer {self._bearer()}"
            try:
                response = self._http.request(method, url, headers=sent, **options)
            except httpx2.TimeoutException:
                # The cause holds the request and its token. Do not chain it.
                raise AppleError(
                    f"{_where(method, url)}: timed out after {self._timeout} s"
                ) from None
            except httpx2.HTTPError as error:
                raise AppleError(f"{_where(method, url)}: {error}") from None
            if response.status_code == 429 and retry and attempt < RETRIES:
                time.sleep(_wait(response))
                continue
            return _check(method, url, response)
        raise AssertionError("unreachable")


def _check(method: str, url: str, response: httpx2.Response) -> httpx2.Response:
    status = response.status_code
    if 200 <= status < 300:
        return response
    if 300 <= status < 400:
        raise ProtocolError(
            f"{_where(method, url)}: refused an HTTP {status} redirect", status=status
        )
    code, detail = _apple_error(response)
    message = f"{_where(method, url)}: HTTP {status} {code or '-'}: {detail or response.reason_phrase}"
    if status == 401:
        raise AuthError(message, status=status, code=code)
    if status == 403:
        kind: type[AppleError] = AgreementError if code == AGREEMENT else AuthError
        raise kind(message, status=status, code=code)
    kind = {404: NotFoundError, 409: ConflictError, 429: RateLimitError}.get(
        status, AppleError
    )
    raise kind(message, status=status, code=code)


def _apple_error(response: httpx2.Response) -> tuple[str | None, str | None]:
    """Read the first entry of `{"errors": [...]}`. Give (None, None) for another body."""
    try:
        body = response.json()
    except ValueError:
        return None, None
    errors = body.get("errors") if isinstance(body, dict) else None
    if not isinstance(errors, list) or not errors or not isinstance(errors[0], dict):
        return None, None
    code, detail = errors[0].get("code"), errors[0].get("detail")
    return (
        code if isinstance(code, str) else None,
        detail if isinstance(detail, str) else None,
    )


def _wait(response: httpx2.Response) -> float:
    try:
        seconds = float(response.headers.get("Retry-After", "1"))
    except ValueError:
        return MAX_WAIT
    return MAX_WAIT if math.isnan(seconds) else min(max(seconds, 0.0), MAX_WAIT)


def _where(method: str, url: str) -> str:
    # The query of an upload URL can carry a signature. Keep it out of messages.
    return f"{method} {url.split('?', 1)[0]}"
