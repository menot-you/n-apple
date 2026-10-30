"""The App Store Connect HTTP client, against the scripted local server."""

from __future__ import annotations

import socket
import time
from collections.abc import Iterator
from pathlib import Path

import jwt
import pytest
from conftest import FakeAsc
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from napple.client import Client
from napple.config import Config
from napple.errors import (
    AgreementError,
    AppleError,
    AuthError,
    ConflictError,
    NotFoundError,
    ProtocolError,
    RateLimitError,
)


@pytest.fixture
def key_text(tmp_path: Path) -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


@pytest.fixture
def config(tmp_path: Path, key_text: str, asc: FakeAsc) -> Config:
    path = tmp_path / "AuthKey_TEST.p8"
    path.write_text(key_text)
    path.chmod(0o600)
    return Config("KEY123", "issuer-uuid", path, api_root=asc.root)


@pytest.fixture
def client(config: Config) -> Iterator[Client]:
    c = Client(config)
    try:
        yield c
    finally:
        c.close()


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    waited: list[float] = []
    monkeypatch.setattr(time, "sleep", waited.append)
    return waited


def bearer(asc: FakeAsc, index: int) -> str:
    return asc.requests[index].headers["Authorization"].removeprefix("Bearer ")


def apple_error(status: int, code: str, detail: str) -> dict[str, object]:
    return {
        "errors": [
            {"status": str(status), "code": code, "title": "t", "detail": detail}
        ]
    }


def test_get_sends_a_valid_bearer_token_and_parses_json(
    asc: FakeAsc, client: Client
) -> None:
    asc.reply("GET", "/v1/apps?limit=1", 200, {"data": [{"id": "1"}]})

    assert client.get("/v1/apps", {"limit": "1"}) == {"data": [{"id": "1"}]}

    claims = jwt.decode(
        bearer(asc, 0),
        options={"verify_signature": False},
        audience="appstoreconnect-v1",
    )
    assert claims["iss"] == "issuer-uuid"


def test_the_token_is_minted_once_and_renewed_before_expiry(
    asc: FakeAsc, client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [1_000_000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    for _ in range(3):
        asc.reply("GET", "/v1/apps", 200, {"data": []})

    client.get("/v1/apps")
    clock[0] += 60
    client.get("/v1/apps")
    clock[0] += 1100
    client.get("/v1/apps")

    assert bearer(asc, 0) == bearer(asc, 1)
    assert bearer(asc, 2) != bearer(asc, 1)


def test_get_all_follows_next_and_stops_at_limit(asc: FakeAsc, client: Client) -> None:
    asc.reply(
        "GET",
        "/v1/builds",
        200,
        {
            "data": [{"id": "1"}, {"id": "2"}],
            "links": {"next": f"{asc.root}/v1/builds?cursor=b"},
        },
    )
    asc.reply(
        "GET",
        "/v1/builds?cursor=b",
        200,
        {
            "data": [{"id": "3"}, {"id": "4"}],
            "links": {"next": f"{asc.root}/v1/builds?cursor=c"},
        },
    )

    items = client.get_all("/v1/builds", limit=3)

    assert [item["id"] for item in items] == ["1", "2", "3"]
    assert len(asc.calls("GET")) == 2


def test_get_all_ends_when_there_is_no_next(asc: FakeAsc, client: Client) -> None:
    asc.reply("GET", "/v1/builds", 200, {"data": [{"id": "1"}], "links": {}})

    assert client.get_all("/v1/builds") == [{"id": "1"}]


@pytest.mark.parametrize(
    "hostile",
    [
        "https://evil.example/v1/builds?cursor=b",
        "{root}/v2/builds?cursor=b",
        "{root}.evil.example/v1/builds",
        "{root}/v1/../v2/builds",
        "{root}/v1/%2e%2e/v2/builds",
    ],
)
def test_a_hostile_next_is_refused_before_any_request(
    asc: FakeAsc, client: Client, hostile: str
) -> None:
    asc.reply(
        "GET",
        "/v1/builds",
        200,
        {"data": [{"id": "1"}], "links": {"next": hostile.format(root=asc.root)}},
    )

    with pytest.raises(ProtocolError):
        client.get_all("/v1/builds")

    assert [r.path for r in asc.requests] == ["/v1/builds"]


def test_a_path_that_leaves_the_api_root_is_refused(
    asc: FakeAsc, client: Client
) -> None:
    with pytest.raises(ProtocolError):
        client.get(".evil.example/v1/apps")

    assert asc.requests == []


def test_429_then_200_succeeds(
    asc: FakeAsc, client: Client, sleeps: list[float]
) -> None:
    asc.reply(
        "GET",
        "/v1/apps",
        429,
        apple_error(429, "RATE_LIMIT_EXCEEDED", "slow"),
        {"Retry-After": "2"},
    )
    asc.reply("GET", "/v1/apps", 200, {"data": []})

    assert client.get("/v1/apps") == {"data": []}
    assert sleeps == [2.0]


def test_429_four_times_raises_rate_limit(
    asc: FakeAsc, client: Client, sleeps: list[float]
) -> None:
    for _ in range(4):
        asc.reply(
            "GET",
            "/v1/apps",
            429,
            apple_error(429, "RATE_LIMIT_EXCEEDED", "slow down"),
            {"Retry-After": "3600"},
        )

    with pytest.raises(RateLimitError) as caught:
        client.get("/v1/apps")

    assert len(asc.calls("GET")) == 4
    assert sleeps == [10.0, 10.0, 10.0]
    assert caught.value.status == 429
    assert caught.value.code == "RATE_LIMIT_EXCEEDED"
    assert "slow down" in str(caught.value)


@pytest.mark.parametrize("method", ["POST", "PATCH"])
def test_writes_are_never_retried(
    asc: FakeAsc, client: Client, sleeps: list[float], method: str
) -> None:
    asc.reply(
        method,
        "/v1/builds/1",
        429,
        apple_error(429, "RATE_LIMIT_EXCEEDED", "x"),
        {"Retry-After": "0"},
    )
    asc.reply(method, "/v1/builds/1", 200, {"data": {}})

    with pytest.raises(RateLimitError):
        if method == "POST":
            client.post("/v1/builds/1", {"data": {}})
        else:
            client.patch("/v1/builds/1", {"data": {}})

    assert len(asc.calls(method)) == 1
    assert sleeps == []


def test_the_403_agreement_case_is_typed_and_fast(asc: FakeAsc, client: Client) -> None:
    asc.reply(
        "GET",
        "/v1/apps",
        403,
        apple_error(
            403,
            "FORBIDDEN.REQUIRED_AGREEMENTS_MISSING_OR_EXPIRED",
            "Sign the agreement.",
        ),
    )

    started = time.monotonic()
    with pytest.raises(AgreementError) as caught:
        client.get("/v1/apps")

    assert time.monotonic() - started < 10
    assert caught.value.status == 403
    assert caught.value.code == "FORBIDDEN.REQUIRED_AGREEMENTS_MISSING_OR_EXPIRED"
    assert "Sign the agreement." in str(caught.value)


@pytest.mark.parametrize(
    ("status", "code", "expected"),
    [
        (401, "NOT_AUTHORIZED", AuthError),
        (403, "FORBIDDEN_ERROR", AuthError),
        (404, "NOT_FOUND", NotFoundError),
        (409, "ENTITY_ERROR.ATTRIBUTE.INVALID", ConflictError),
        (500, "UNEXPECTED_ERROR", AppleError),
    ],
)
def test_asc_errors_map_to_typed_errors(
    asc: FakeAsc, client: Client, status: int, code: str, expected: type[AppleError]
) -> None:
    asc.reply("GET", "/v1/apps", status, apple_error(status, code, f"detail {status}"))

    with pytest.raises(AppleError) as caught:
        client.get("/v1/apps")

    assert type(caught.value) is expected
    assert caught.value.status == status
    assert caught.value.code == code
    assert f"detail {status}" in str(caught.value)


def test_an_error_body_that_is_not_json_still_maps_by_status(
    asc: FakeAsc, client: Client
) -> None:
    asc.reply("GET", "/v1/apps", 404, b"<html>gone</html>")

    with pytest.raises(NotFoundError) as caught:
        client.get("/v1/apps")

    assert caught.value.status == 404
    assert caught.value.code is None


def test_a_2xx_body_that_is_not_json_is_a_protocol_error(
    asc: FakeAsc, client: Client
) -> None:
    asc.reply("GET", "/v1/apps", 200, b"<html>not json</html>")

    with pytest.raises(ProtocolError):
        client.get("/v1/apps")


def test_a_redirect_on_post_is_not_replayed(asc: FakeAsc, client: Client) -> None:
    asc.reply("POST", "/v1/builds", 307, {}, {"Location": f"{asc.root}/v1/other"})

    with pytest.raises(ProtocolError):
        client.post("/v1/builds", {"data": {}})

    assert len(asc.calls("POST")) == 1
    assert asc.calls("POST", "/v1/other") == []


def test_post_and_patch_send_json_and_patch_accepts_204(
    asc: FakeAsc, client: Client
) -> None:
    asc.reply("POST", "/v1/builds", 201, {"data": {"id": "9"}})
    asc.reply("PATCH", "/v1/builds/9", 200, {"data": {"id": "9"}})
    asc.reply("PATCH", "/v1/builds/8", 204, b"")

    assert client.post("/v1/builds", {"data": {"type": "builds"}}) == {
        "data": {"id": "9"}
    }
    assert client.patch("/v1/builds/9", {"data": {}}) == {"data": {"id": "9"}}
    assert client.patch("/v1/builds/8", {"data": {}}) == {}

    post = asc.calls("POST")[0]
    assert post.body == b'{"data":{"type":"builds"}}'
    assert post.headers["Content-Type"] == "application/json"


def test_put_part_sends_no_token_and_is_not_retried(
    asc: FakeAsc, client: Client, sleeps: list[float]
) -> None:
    asc.reply("PUT", "/upload/part1", 200, b"")
    asc.reply("PUT", "/upload/part2", 429, b"", {"Retry-After": "0"})

    client.put_part(
        f"{asc.root}/upload/part1",
        "PUT",
        {"Content-Type": "application/octet-stream"},
        b"\x00\x01",
    )
    with pytest.raises(RateLimitError):
        client.put_part(f"{asc.root}/upload/part2", "PUT", {}, b"x")

    first = asc.calls("PUT", "/upload/part1")[0]
    assert first.body == b"\x00\x01"
    assert first.headers["Content-Type"] == "application/octet-stream"
    assert all("Authorization" not in r.headers for r in asc.requests)
    assert len(asc.calls("PUT", "/upload/part2")) == 1
    assert sleeps == []


def test_a_silent_server_is_a_timeout_error(key_text: str, tmp_path: Path) -> None:
    path = tmp_path / "k.p8"
    path.write_text(key_text)
    path.chmod(0o600)
    with socket.socket() as silent:
        silent.bind(("127.0.0.1", 0))
        silent.listen()
        root = f"http://127.0.0.1:{silent.getsockname()[1]}"
        c = Client(Config("K", "I", path, api_root=root), timeout=0.3)
        try:
            started = time.monotonic()
            with pytest.raises(AppleError) as caught:
                c.get("/v1/apps")
        finally:
            c.close()

    assert time.monotonic() - started < 5
    assert "timed out" in str(caught.value)


def test_the_key_and_the_token_are_absent_from_errors_and_repr(
    asc: FakeAsc, client: Client, key_text: str
) -> None:
    asc.reply("GET", "/v1/apps", 401, apple_error(401, "NOT_AUTHORIZED", "bad token"))

    with pytest.raises(AuthError) as caught:
        client.get("/v1/apps")

    sent = bearer(asc, 0)
    body = "".join(key_text.splitlines()[1:-1])
    for text in (str(caught.value), repr(caught.value), repr(client)):
        assert sent not in text
        assert body not in text
        assert "BEGIN PRIVATE KEY" not in text
