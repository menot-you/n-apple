"""upload_build with the real Client against the scripted local ASC server."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import pytest
from conftest import FakeAsc
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from napple import upload as upload_module
from napple.client import Client
from napple.config import Config
from napple.errors import UploadError
from napple.upload import upload_build

UPLOAD = "/v1/buildUploads"
FILES = "/v1/buildUploadFiles"
POLL = "/v1/buildUploads/BU1?include=build"
FILE_LIST = "/v1/buildUploads/BU1/buildUploadFiles"
READBACK = "/v1/apps/123/buildUploads?" + urlencode(
    {
        "filter[cfBundleShortVersionString]": "1.2.0",
        "filter[cfBundleVersion]": "42",
        "filter[platform]": "IOS",
        "include": "build",
    }
)
BUILD = {
    "type": "builds",
    "id": "B9",
    "attributes": {"version": "42", "processingState": "PROCESSING"},
}


@pytest.fixture
def client(tmp_path: Path, asc: FakeAsc) -> Iterator[Client]:
    key = tmp_path / "AuthKey_TEST.p8"
    key.write_bytes(
        ec.generate_private_key(ec.SECP256R1()).private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key.chmod(0o600)
    c = Client(Config("KEY123", "issuer-uuid", key, api_root=asc.root))
    try:
        yield c
    finally:
        c.close()


@pytest.fixture(autouse=True)
def plain_http_parts(monkeypatch: pytest.MonkeyPatch) -> None:
    # The scripted server is plain HTTP. One test sets the product value back.
    monkeypatch.setattr(upload_module, "PART_SCHEMES", frozenset({"https", "http"}))


def no_sleep(seconds: float) -> None:
    pass


@pytest.fixture
def ipa(tmp_path: Path) -> Path:
    path = tmp_path / "App.ipa"
    path.write_bytes(bytes(range(256)) * 4 + b"tail")  # 1028 bytes
    return path


def upload(name: str, *, build: bool = False, **extra: Any) -> dict[str, Any]:
    resource: dict[str, Any] = {
        "type": "buildUploads",
        "id": "BU1",
        "attributes": {"state": {"state": name, **extra}},
        "relationships": {},
    }
    if build:
        resource["relationships"]["build"] = {"data": {"type": "builds", "id": "B9"}}
    return resource


def readback(*resources: dict[str, Any]) -> dict[str, Any]:
    return {"data": list(resources), "included": [BUILD], "links": {}}


def files(asc: FakeAsc, *deliveries: str) -> None:
    """Queue the file list of BU1: one ASSET file for each delivery state."""
    asc.reply(
        "GET",
        FILE_LIST,
        200,
        {
            "data": [
                {
                    "type": "buildUploadFiles",
                    "id": f"BUF{number}",
                    "attributes": {
                        "assetType": "ASSET",
                        "assetDeliveryState": {"state": delivery},
                    },
                }
                for number, delivery in enumerate(deliveries, start=1)
            ],
            "links": {},
        },
    )


def polled(name: str, **extra: Any) -> dict[str, Any]:
    return {
        "data": upload(name, build=name == "COMPLETE", **extra),
        "included": [BUILD],
    }


def script_file(
    asc: FakeAsc,
    path: Path,
    parts: list[tuple[int, int]] | None = None,
    method: str = "PUT",
) -> None:
    """Queue the file POST, the parts ([0, 600) and [600, size) by default) and the PATCH."""
    size = path.stat().st_size
    operations: list[dict[str, Any]] = [
        {
            "method": method,
            "url": f"{asc.root}/part/{number}",
            "offset": offset,
            "length": length,
            "requestHeaders": [],
        }
        for number, (offset, length) in enumerate(
            parts or [(0, 600), (600, size - 600)], start=1
        )
    ]
    operations[0]["requestHeaders"] = [
        {"name": "Content-Type", "value": "application/octet-stream"}
    ]
    asc.reply(
        "POST",
        FILES,
        201,
        {
            "data": {
                "type": "buildUploadFiles",
                "id": "BUF1",
                "attributes": {"uploadOperations": operations},
            }
        },
    )
    for number in range(1, len(operations) + 1):
        asc.reply(method, f"/part/{number}")
    asc.reply(
        "PATCH",
        FILES + "/BUF1",
        200,
        {"data": {"type": "buildUploadFiles", "id": "BUF1"}},
    )


def script(asc: FakeAsc, path: Path) -> None:
    """Queue an empty readback, the upload POST and the file steps."""
    asc.reply("GET", READBACK, 200, readback())
    asc.reply("POST", UPLOAD, 201, {"data": upload("AWAITING_UPLOAD")})
    script_file(asc, path)


def run(client: Client, path: Path, **overrides: Any) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "app_id": "123",
        "version": "1.2.0",
        "build_number": "42",
        "platform": "IOS",
        "poll_seconds": 1.0,
        "timeout_seconds": 10.0,
        "sleep": no_sleep,
    }
    arguments.update(overrides)
    return upload_build(client, path, **arguments)


def writes(asc: FakeAsc) -> list[str]:
    return [f"{r.method} {r.path}" for r in asc.requests if r.method != "GET"]


def test_happy_path_uploads_two_parts_and_returns_the_build(
    client: Client, asc: FakeAsc, ipa: Path
) -> None:
    script(asc, ipa)
    asc.reply("GET", POLL, 200, polled("PROCESSING"))
    asc.reply("GET", POLL, 200, polled("COMPLETE"))

    result = run(client, ipa)

    assert result["id"] == "BU1"
    assert result["state"]["state"] == "COMPLETE"
    assert result["build_id"] == "B9"
    assert result["build"] == {
        "id": "B9",
        "version": "42",
        "processingState": "PROCESSING",
    }
    content = ipa.read_bytes()
    assert [r.body for r in asc.calls("PUT")] == [content[:600], content[600:]]
    assert (
        asc.calls("PUT", "/part/1")[0].headers["Content-Type"]
        == "application/octet-stream"
    )
    assert "Authorization" not in asc.calls("PUT", "/part/1")[0].headers
    assert len(asc.calls("POST", UPLOAD)) == 1
    assert len(asc.calls("POST", FILES)) == 1

    created = json.loads(asc.calls("POST", UPLOAD)[0].body)["data"]
    assert created["attributes"] == {
        "cfBundleShortVersionString": "1.2.0",
        "cfBundleVersion": "42",
        "platform": "IOS",
    }
    assert created["relationships"]["app"]["data"] == {"type": "apps", "id": "123"}

    file = json.loads(asc.calls("POST", FILES)[0].body)["data"]
    assert file["attributes"] == {
        "fileName": "App.ipa",
        "fileSize": len(content),
        "uti": "com.apple.ipa",
        "assetType": "ASSET",
    }
    assert file["relationships"]["buildUpload"]["data"] == {
        "type": "buildUploads",
        "id": "BU1",
    }

    patches = asc.calls("PATCH")
    assert len(patches) == 1
    assert json.loads(patches[0].body)["data"] == {
        "type": "buildUploadFiles",
        "id": "BUF1",
        "attributes": {
            "uploaded": True,
            "sourceFileChecksums": {
                "file": {"hash": hashlib.md5(content).hexdigest(), "algorithm": "MD5"}
            },
        },
    }


def test_part_failure_sends_no_patch(client: Client, asc: FakeAsc, ipa: Path) -> None:
    script(asc, ipa)
    asc.replies[("PUT", "/part/2")] = []
    asc.reply("PUT", "/part/2", 500)

    with pytest.raises(UploadError, match=r"BU1: operation 1 failed"):
        run(client, ipa)

    assert asc.calls("PATCH") == []
    assert asc.calls("GET", UPLOAD) == []


def test_failed_state_raises_with_apple_details(
    client: Client, asc: FakeAsc, ipa: Path
) -> None:
    script(asc, ipa)
    asc.reply(
        "GET",
        POLL,
        200,
        polled("FAILED", errors=[{"code": "90062", "description": "bad version"}]),
    )

    with pytest.raises(UploadError, match=r"BU1.*FAILED.*90062.*bad version"):
        run(client, ipa)


def test_timeout_names_the_last_state(client: Client, asc: FakeAsc, ipa: Path) -> None:
    script(asc, ipa)
    for _ in range(4):
        asc.reply("GET", POLL, 200, polled("PROCESSING"))
    slept: list[float] = []

    with pytest.raises(UploadError, match=r"BU1.*PROCESSING"):
        run(client, ipa, poll_seconds=1.0, timeout_seconds=3.0, sleep=slept.append)

    assert slept == [1.0, 1.0, 1.0]
    assert len(asc.calls("GET", UPLOAD)) == 4


def test_step_failure_after_the_post_names_the_upload(
    client: Client, asc: FakeAsc, ipa: Path
) -> None:
    asc.reply("GET", READBACK, 200, readback())
    asc.reply("POST", UPLOAD, 201, {"data": upload("AWAITING_UPLOAD")})
    asc.reply("POST", FILES, 409)

    with pytest.raises(UploadError, match="BU1"):
        run(client, ipa)

    assert len(asc.calls("POST", FILES)) == 1


def test_existing_complete_upload_returns_without_a_write(
    client: Client, asc: FakeAsc, ipa: Path
) -> None:
    asc.reply("GET", READBACK, 200, readback(upload("COMPLETE", build=True)))

    result = run(client, ipa)

    assert result["id"] == "BU1"
    assert result["build"] == {
        "id": "B9",
        "version": "42",
        "processingState": "PROCESSING",
    }
    assert writes(asc) == []


def test_existing_processing_upload_is_polled_without_a_post(
    client: Client, asc: FakeAsc, ipa: Path
) -> None:
    asc.reply("GET", READBACK, 200, readback(upload("PROCESSING")))
    asc.reply("GET", POLL, 200, polled("COMPLETE"))

    result = run(client, ipa)

    assert result["state"]["state"] == "COMPLETE"
    assert result["build_id"] == "B9"
    assert writes(asc) == []


def test_existing_failed_upload_raises_without_a_write(
    client: Client, asc: FakeAsc, ipa: Path
) -> None:
    asc.reply("GET", READBACK, 200, readback(upload("FAILED")))

    with pytest.raises(UploadError, match=r"BU1.*FAILED"):
        run(client, ipa)

    assert writes(asc) == []


def test_post_timeout_then_a_second_call_posts_the_upload_once(
    client: Client, asc: FakeAsc, ipa: Path
) -> None:
    asc.reply("GET", READBACK, 200, readback())
    asc.reply("POST", UPLOAD, 504)

    with pytest.raises(UploadError, match="reads it back"):
        run(client, ipa)

    # Apple created the upload, but the reply was lost: no file is reserved yet.
    asc.reply("GET", READBACK, 200, readback(upload("AWAITING_UPLOAD")))
    files(asc)
    script_file(asc, ipa)
    asc.reply("GET", POLL, 200, polled("COMPLETE"))

    result = run(client, ipa)

    assert result["id"] == "BU1"
    assert len(asc.calls("POST", UPLOAD)) == 1
    assert len(asc.calls("POST", FILES)) == 1


def test_poll_failure_then_a_second_call_resumes_without_a_post(
    client: Client, asc: FakeAsc, ipa: Path
) -> None:
    script(asc, ipa)
    asc.reply("GET", POLL, 500)

    with pytest.raises(UploadError, match="BU1"):
        run(client, ipa)

    asc.reply("GET", READBACK, 200, readback(upload("AWAITING_UPLOAD")))
    files(asc, "UPLOAD_COMPLETE")
    asc.reply("GET", POLL, 200, polled("COMPLETE"))
    before = writes(asc)

    result = run(client, ipa)

    assert result["state"]["state"] == "COMPLETE"
    assert writes(asc) == before
    assert len(asc.calls("POST", UPLOAD)) == 1


def test_reserved_file_without_a_commit_raises_without_a_write(
    client: Client, asc: FakeAsc, ipa: Path
) -> None:
    # The upload has no assetFile link: only the file list shows the reserved file.
    asc.reply("GET", READBACK, 200, readback(upload("AWAITING_UPLOAD")))
    files(asc, "AWAITING_UPLOAD")

    with pytest.raises(UploadError, match=r"BU1.*BUF1.*new build number"):
        run(client, ipa)

    assert writes(asc) == []


@pytest.mark.parametrize(
    ("parts", "method"),
    [
        pytest.param([(0, 514), (0, 514)], "PUT", id="same-range-twice"),
        pytest.param([(0, 600), (500, 528)], "PUT", id="overlap"),
        pytest.param([(0, 500), (600, 428)], "PUT", id="gap"),
        pytest.param([(0, 600)], "PUT", id="short-of-the-end"),
        pytest.param([(4, 1024)], "PUT", id="not-from-zero"),
        pytest.param(None, "DELETE", id="method"),
    ],
)
def test_bad_operations_send_no_part_and_no_patch(
    client: Client,
    asc: FakeAsc,
    ipa: Path,
    parts: list[tuple[int, int]] | None,
    method: str,
) -> None:
    asc.reply("GET", READBACK, 200, readback())
    asc.reply("POST", UPLOAD, 201, {"data": upload("AWAITING_UPLOAD")})
    script_file(asc, ipa, parts, method)

    with pytest.raises(UploadError, match="BU1"):
        run(client, ipa)

    assert writes(asc) == ["POST /v1/buildUploads", "POST /v1/buildUploadFiles"]


def test_plain_http_part_url_is_refused(
    client: Client, asc: FakeAsc, ipa: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(upload_module, "PART_SCHEMES", frozenset({"https"}))
    script(asc, ipa)

    with pytest.raises(UploadError, match=r"BU1.*https"):
        run(client, ipa)

    assert writes(asc) == ["POST /v1/buildUploads", "POST /v1/buildUploadFiles"]


@pytest.mark.parametrize(
    ("name", "overrides"),
    [
        ("App.zip", {}),
        ("App.ipa", {"build_number": "042"}),
        ("App.ipa", {"build_number": "1."}),
        ("App.ipa", {"platform": "WATCH_OS"}),
        ("missing.ipa", {}),
        ("dir.ipa", {}),
        ("empty.ipa", {}),
        ("App.ipa", {"app_id": "12/../34"}),
        ("App.ipa", {"app_id": "abc"}),
    ],
)
def test_invalid_input_sends_no_request(
    client: Client,
    asc: FakeAsc,
    tmp_path: Path,
    name: str,
    overrides: dict[str, Any],
) -> None:
    (tmp_path / "App.zip").write_bytes(b"x")
    (tmp_path / "App.ipa").write_bytes(b"x")
    (tmp_path / "dir.ipa").mkdir()
    (tmp_path / "empty.ipa").write_bytes(b"")

    with pytest.raises(UploadError):
        run(client, tmp_path / name, **overrides)

    assert asc.requests == []


@pytest.fixture
def root(tmp_path: Path) -> Path:
    path = tmp_path / "workspace"
    path.mkdir()
    return path


def test_upload_root_reads_a_file_inside_the_root_through_one_descriptor(
    client: Client,
    asc: FakeAsc,
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ipa = root / "App.ipa"
    ipa.write_bytes(bytes(range(256)) * 4 + b"tail")
    content = ipa.read_bytes()
    script(asc, ipa)
    asc.reply("GET", POLL, 200, polled("COMPLETE"))
    opened: list[str] = []
    real_open = upload_module.os.open

    def counted(path: Any, *args: Any, **kwargs: Any) -> int:
        if Path(path) == ipa:
            opened.append(str(path))
        return real_open(path, *args, **kwargs)

    real_path_open = Path.open

    def refused(self: Path, *args: Any, **kwargs: Any) -> Any:
        assert self != ipa, f"the path was opened again: {self}"
        return real_path_open(self, *args, **kwargs)

    monkeypatch.setattr(upload_module.os, "open", counted)
    monkeypatch.setattr(Path, "open", refused)

    result = run(client, ipa, upload_root=root)

    assert result["state"]["state"] == "COMPLETE"
    assert opened == [str(ipa)]
    assert [r.body for r in asc.calls("PUT")] == [content[:600], content[600:]]
    patch = json.loads(asc.calls("PATCH")[0].body)["data"]["attributes"]
    assert patch["sourceFileChecksums"]["file"]["hash"] == (
        hashlib.md5(content).hexdigest()
    )


@pytest.mark.parametrize("kind", ["symlink", "tmp", "directory", "fifo"])
def test_upload_root_refuses_a_path_that_is_not_a_regular_file_inside_it(
    client: Client, asc: FakeAsc, root: Path, tmp_path: Path, kind: str
) -> None:
    outside = tmp_path / "outside.ipa"
    outside.write_bytes(b"outside bytes")
    with tempfile.NamedTemporaryFile(dir="/tmp", suffix=".ipa") as loose:
        loose.write(b"loose bytes")
        loose.flush()
        path = {
            "symlink": root / "App.ipa",
            "tmp": Path(loose.name),
            "directory": root / "dir.ipa",
            "fifo": root / "fifo.ipa",
        }[kind]
        if kind == "symlink":
            path.symlink_to(outside)
        elif kind == "directory":
            path.mkdir()
        elif kind == "fifo":
            os.mkfifo(path)

        with pytest.raises(UploadError, match="regular file inside the upload root"):
            run(client, path, upload_root=root)

    assert asc.requests == []


def test_upload_root_refuses_without_proc_self_fd(
    client: Client,
    asc: FakeAsc,
    root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ipa = root / "App.ipa"
    ipa.write_bytes(b"bytes")
    monkeypatch.setattr(upload_module, "DESCRIPTORS", tmp_path / "no-proc")

    with pytest.raises(UploadError, match="/proc/self/fd"):
        run(client, ipa, upload_root=root)

    assert asc.requests == []
