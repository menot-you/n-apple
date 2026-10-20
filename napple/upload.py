"""Upload one IPA or PKG through the buildUploads API (App Store Connect API 4.5.1)."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlsplit

from .errors import AppleError, UploadError

if TYPE_CHECKING:
    from .client import Client

# Platform enum.
PLATFORMS = frozenset({"IOS", "MAC_OS", "TV_OS", "VISION_OS"})
# BuildUploadFileCreateRequest.attributes.uti enum.
UTIS = {".ipa": "com.apple.ipa", ".pkg": "com.apple.pkg"}
# The schemes an upload operation URL may use.
PART_SCHEMES = frozenset({"https"})
APP_ID = re.compile(r"[0-9]+")
BUILD_NUMBER = re.compile(r"[1-9][0-9]*(\.[0-9]+)*")
# Where the kernel names the file an open descriptor holds.
DESCRIPTORS = Path("/proc/self/fd")
# The bytes of one read of the checksum.
CHUNK = 1 << 20


def upload_build(
    client: Client,
    path: Path,
    *,
    app_id: str,
    version: str,
    build_number: str,
    platform: str,
    poll_seconds: float = 15.0,
    timeout_seconds: float = 3600.0,
    sleep: Callable[[float], None] = time.sleep,
    upload_root: Path | None = None,
) -> dict[str, Any]:
    """Upload the file and wait for a terminal state. A repeated call resumes.

    Before any write, read back the uploads of this app, version, build number
    and platform. A COMPLETE upload is returned with no write. A FAILED upload is
    an UploadError: a new build number is the caller's decision. An upload in
    progress is resumed. Only when none exists is a new upload created.

    Return {"id", "state", "build_id", "build"}: "state" is the BuildUpload state
    object (state, errors, warnings, infos). "build" is {"id", "version",
    "processingState"}. "build_id" and "build" are None when Apple gives no build.
    POST and PATCH are not retried. A failure after Apple created the upload is an
    UploadError that names it. A next call resumes an upload with no file or with
    a committed file. A file that is reserved but not committed stops the call:
    the caller uses a new build number.

    With `upload_root`, the path is opened once and must be a regular file inside
    that root. Every byte comes from that descriptor, never from the path again.
    """
    uti = UTIS.get(path.suffix.lower())
    if uti is None:
        raise UploadError(f"{path}: the file must have a .ipa or .pkg suffix")
    if upload_root is None and not path.is_file():
        raise UploadError(f"{path}: the file does not exist or is not a regular file")
    if not BUILD_NUMBER.fullmatch(build_number):
        raise UploadError(f"build_number {build_number!r} is not a CFBundleVersion")
    if platform not in PLATFORMS:
        raise UploadError(f"platform {platform!r} is not one of {sorted(PLATFORMS)}")
    if not APP_ID.fullmatch(app_id):
        raise UploadError(f"app_id {app_id!r} is not a numeric App Store Connect id")
    if not version:
        raise UploadError("version must not be empty")
    if upload_root is None:
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
        except OSError as error:
            raise UploadError(f"{path}: the file cannot be opened: {error}") from error
    else:
        descriptor = _confined(path, upload_root)
    try:
        size = os.fstat(descriptor).st_size
        if size < 1:
            raise UploadError(f"{path}: the file is empty")
        return _upload(
            client,
            descriptor,
            path.name,
            uti,
            size,
            app_id=app_id,
            version=version,
            build_number=build_number,
            platform=platform,
            poll_seconds=poll_seconds,
            timeout_seconds=timeout_seconds,
            sleep=sleep,
        )
    finally:
        os.close(descriptor)


def _confined(path: Path, root: Path) -> int:
    """Open `path` once, and keep it only as a regular file inside `root`."""
    if not DESCRIPTORS.is_dir():
        raise UploadError("/proc/self/fd is missing, so the upload root cannot apply")
    refused = UploadError(
        f"{path}: the file must be a regular file inside the upload root {root}"
    )
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError as error:
        raise refused from error
    try:
        opened = Path(os.readlink(DESCRIPTORS / str(descriptor)))
        if not stat.S_ISREG(os.fstat(descriptor).st_mode) or not opened.is_relative_to(
            root.resolve()
        ):
            raise refused
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _upload(
    client: Client,
    descriptor: int,
    file_name: str,
    uti: str,
    size: int,
    *,
    app_id: str,
    version: str,
    build_number: str,
    platform: str,
    poll_seconds: float,
    timeout_seconds: float,
    sleep: Callable[[float], None],
) -> dict[str, Any]:
    where = "build upload readback"
    try:
        # Step 0: GET /v1/apps/{id}/buildUploads (BuildUploadsResponse) with the
        # filters cfBundleShortVersionString, cfBundleVersion and platform.
        found = client.get(
            f"/v1/apps/{quote(app_id, safe='')}/buildUploads",
            {
                "filter[cfBundleShortVersionString]": version,
                "filter[cfBundleVersion]": build_number,
                "filter[platform]": platform,
                "include": "build",
            },
        )
        # Prefer COMPLETE, then an upload in progress, then FAILED.
        rank = {"COMPLETE": 0, "FAILED": 2}
        uploads = sorted(found["data"], key=lambda r: rank.get(_state(r) or "", 1))
        if uploads:
            upload = uploads[0]
            where = f"build upload {upload['id']}"
            name = _state(upload)
            if name == "COMPLETE":
                return _result(upload, found)
            if name == "FAILED":
                raise UploadError(f"{where}: state FAILED: {_errors(upload)}")
            send = False
            if name == "AWAITING_UPLOAD":
                # GET /v1/buildUploads/{id}/buildUploadFiles (BuildUploadFilesResponse).
                # The assetFile link is not proof: the spec does not say when Apple sets it.
                assets = [
                    file
                    for file in client.get_all(
                        f"/v1/buildUploads/{quote(upload['id'], safe='')}/buildUploadFiles"
                    )
                    if file["attributes"].get("assetType") == "ASSET"
                ]
                send = not assets
                for file in assets:
                    delivery = file["attributes"]["assetDeliveryState"]["state"]
                    if delivery not in ("UPLOAD_COMPLETE", "COMPLETE"):
                        raise UploadError(
                            f"{where}: the file {file['id']} has delivery state"
                            f" {delivery}, so it is reserved but not committed."
                            " This call stops. Use a new build number."
                        )
        else:
            # Step 1: POST /v1/buildUploads (BuildUploadCreateRequest).
            try:
                created = client.post(
                    "/v1/buildUploads",
                    {
                        "data": {
                            "type": "buildUploads",
                            "attributes": {
                                "cfBundleShortVersionString": version,
                                "cfBundleVersion": build_number,
                                "platform": platform,
                            },
                            "relationships": {
                                "app": {"data": {"type": "apps", "id": app_id}}
                            },
                        }
                    },
                )
            except AppleError as error:
                raise UploadError(
                    f"POST /v1/buildUploads failed, so Apple possibly created the"
                    f" upload. A new call reads it back first: {error}",
                    status=error.status,
                    code=error.code,
                ) from error
            upload = created["data"]
            where = f"build upload {upload['id']}"
            send = True
        if send:
            _send(client, descriptor, file_name, upload["id"], uti, size)
        return _poll(client, upload["id"], poll_seconds, timeout_seconds, sleep)
    except UploadError:
        raise
    except (AppleError, OSError, KeyError, TypeError, ValueError) as error:
        raise UploadError(
            f"{where}: {type(error).__name__}: {error}",
            status=getattr(error, "status", None),
            code=getattr(error, "code", None),
        ) from error


def _send(
    client: Client, descriptor: int, name: str, upload_id: str, uti: str, size: int
) -> None:
    # Step 2: POST /v1/buildUploadFiles (BuildUploadFileCreateRequest); the reply
    # is a BuildUploadFile with uploadOperations (DeliveryFileUploadOperation).
    file = client.post(
        "/v1/buildUploadFiles",
        {
            "data": {
                "type": "buildUploadFiles",
                "attributes": {
                    "fileName": name,
                    "fileSize": size,
                    "uti": uti,
                    "assetType": "ASSET",
                },
                "relationships": {
                    "buildUpload": {"data": {"type": "buildUploads", "id": upload_id}}
                },
            }
        },
    )["data"]
    file_id: str = file["id"]
    operations: list[dict[str, Any]] = file["attributes"]["uploadOperations"]
    if not operations:
        raise UploadError(f"build upload {upload_id}: Apple gave no upload operations")
    # The parts, in offset order, must cover [0, size) once, with no gap and no overlap.
    end = 0
    for operation in sorted(operations, key=lambda o: int(o["offset"])):
        offset, length = int(operation["offset"]), int(operation["length"])
        if offset != end or length < 1:
            raise UploadError(
                f"build upload {upload_id}: the upload operations do not cover the"
                f" file once: a part starts at {offset}, expected {end}"
            )
        end = offset + length
        if urlsplit(operation["url"]).scheme not in PART_SCHEMES:
            raise UploadError(
                f"build upload {upload_id}: an upload operation URL is not https"
            )
        if operation["method"] not in ("PUT", "POST"):
            raise UploadError(
                f"build upload {upload_id}: the upload operation method"
                f" {operation['method']!r} is not PUT or POST"
            )
    if end != size:
        raise UploadError(
            f"build upload {upload_id}: the upload operations end at {end},"
            f" the file size is {size}"
        )

    # Step 3: send [offset, offset + length) of the file for each
    # DeliveryFileUploadOperation, with its requestHeaders (HttpHeader).
    for index, operation in enumerate(operations):
        offset, length = int(operation["offset"]), int(operation["length"])
        part = os.pread(descriptor, length, offset)
        if len(part) != length:
            raise UploadError(
                f"build upload {upload_id}: operation {index} asks for bytes"
                f" [{offset}, {offset + length}) outside the file"
            )
        headers = {h["name"]: h["value"] for h in operation.get("requestHeaders") or []}
        try:
            client.put_part(operation["url"], operation["method"], headers, part)
        except AppleError as error:
            raise UploadError(
                f"build upload {upload_id}: operation {index} failed: {error}",
                status=error.status,
                code=error.code,
            ) from error
    checksum = hashlib.md5()
    offset = 0
    while chunk := os.pread(descriptor, CHUNK, offset):
        checksum.update(chunk)
        offset += len(chunk)
    digest = checksum.hexdigest()

    # Step 4: PATCH /v1/buildUploadFiles/{id} (BuildUploadFileUpdateRequest)
    # with uploaded and sourceFileChecksums.file (Checksums, ChecksumAlgorithm MD5).
    client.patch(
        f"/v1/buildUploadFiles/{file_id}",
        {
            "data": {
                "type": "buildUploadFiles",
                "id": file_id,
                "attributes": {
                    "uploaded": True,
                    "sourceFileChecksums": {
                        "file": {"hash": digest, "algorithm": "MD5"}
                    },
                },
            }
        },
    )


def _poll(
    client: Client,
    upload_id: str,
    poll_seconds: float,
    timeout_seconds: float,
    sleep: Callable[[float], None],
) -> dict[str, Any]:
    # Step 5: GET /v1/buildUploads/{id} (BuildUploadResponse) until
    # attributes.state.state (BuildUploadState) is COMPLETE or FAILED.
    # The timeout counts the sleep time only, so an injected sleep controls it.
    waited = 0.0
    while True:
        document = client.get(f"/v1/buildUploads/{upload_id}", {"include": "build"})
        upload = document["data"]
        name = _state(upload)
        if name == "COMPLETE":
            return _result(upload, document)
        if name == "FAILED":
            raise UploadError(
                f"build upload {upload_id}: state FAILED: {_errors(upload)}"
            )
        if waited >= timeout_seconds:
            raise UploadError(
                f"build upload {upload_id}: timeout after {waited:g} s,"
                f" the last state is {name}"
            )
        sleep(poll_seconds)
        waited += poll_seconds


def _state(upload: dict[str, Any]) -> str | None:
    state: str | None = upload["attributes"]["state"].get("state")
    return state


def _errors(upload: dict[str, Any]) -> str:
    details = upload["attributes"]["state"].get("errors") or []
    return "; ".join(f"{e.get('code')}: {e.get('description')}" for e in details)


def _related(
    upload: dict[str, Any], document: dict[str, Any], relationship: str
) -> dict[str, Any] | None:
    """Find the included resource that the relationship names, or None."""
    link = ((upload.get("relationships") or {}).get(relationship) or {}).get("data")
    if not link:
        return None
    for item in document.get("included") or []:
        if item.get("type") == link["type"] and item.get("id") == link["id"]:
            found: dict[str, Any] = item
            return found
    return None


def _result(upload: dict[str, Any], document: dict[str, Any]) -> dict[str, Any]:
    build = _related(upload, document, "build")
    attributes = (build or {}).get("attributes") or {}
    return {
        "id": upload["id"],
        "state": upload["attributes"]["state"],
        "build_id": None if build is None else build["id"],
        "build": None
        if build is None
        else {
            "id": build["id"],
            "version": attributes.get("version"),
            "processingState": attributes.get("processingState"),
        },
    }
