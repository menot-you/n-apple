"""The ten App Store Connect tools, and the one call that runs them.

The tool list needs no API key. The first known call builds the client, so a
missing key fails that call, not the server start.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .client import Client
from .errors import NotFoundError
from .upload import upload_build

ASK = "Creates or uploads a real Apple asset. Ask the owner before each call."
READ = {"readOnlyHint": True}
WRITE = {"readOnlyHint": False, "destructiveHint": True}
# The most related items a list call asks for. A list that reaches it may be cut.
RELATION_LIMIT = 50


def _tool(
    name: str,
    description: str,
    properties: dict[str, Any] | None = None,
    required: list[str] | None = None,
    *,
    write: bool = False,
) -> dict[str, Any]:
    """One MCP tool definition. A write tool always carries the owner's rule."""
    return {
        "name": name,
        "description": f"{description} {ASK}" if write else description,
        "inputSchema": {
            "type": "object",
            "properties": properties or {},
            "required": required or [],
        },
        "annotations": WRITE if write else READ,
    }


def _str(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description}


def _strs(description: str) -> dict[str, Any]:
    return {"type": "array", "items": {"type": "string"}, "description": description}


TOOLS: list[dict[str, Any]] = [
    _tool(
        "napple_account_show", "Check the API key. Returns how many apps it can see."
    ),
    _tool(
        "napple_app_list",
        "List the apps the API key can see: id, bundle id, name and SKU.",
    ),
    _tool(
        "napple_build_list",
        "List the builds of one app, newest first, with processing and beta state.",
        {
            "app_id": _str("The app id from napple_app_list."),
            "version": _str("Version name to filter by, for example 1.2.0."),
            "platform": _str("IOS, MAC_OS, TV_OS or VISION_OS."),
            "limit": {
                "type": "integer",
                "description": "Builds to return, 1 to 200. Default 20.",
            },
        },
        ["app_id"],
    ),
    _tool(
        "napple_build_show",
        "Show one build by its id, with processing and beta state.",
        {"build_id": _str("The build id from napple_build_list.")},
        ["build_id"],
    ),
    _tool(
        "napple_signing_show",
        "Show one bundle id: its capabilities, its profiles and all certificates.",
        {"bundle_id": _str("The bundle identifier, for example com.example.app.")},
        ["bundle_id"],
    ),
    _tool(
        "napple_group_list",
        "List the TestFlight beta groups of one app.",
        {"app_id": _str("The app id from napple_app_list.")},
        ["app_id"],
    ),
    _tool(
        "napple_build_upload",
        "Upload a build file to App Store Connect.",
        {
            "path": _str("Local path of the .ipa or .pkg file."),
            "app_id": _str("The app id from napple_app_list."),
            "version": _str("Version name, for example 1.2.0."),
            "build_number": _str("Build number, for example 42."),
            "platform": _str("IOS, MAC_OS, TV_OS or VISION_OS."),
        },
        ["path", "app_id", "version", "build_number", "platform"],
        write=True,
    ),
    _tool(
        "napple_bundle_create",
        "Register a bundle id, then add its capabilities.",
        {
            "identifier": _str("The bundle identifier, for example com.example.app."),
            "name": _str("The name shown in the developer portal."),
            "platform": _str("IOS, MAC_OS or UNIVERSAL."),
            "capabilities": _strs("Capability types, for example PUSH_NOTIFICATIONS."),
        },
        ["identifier", "name", "platform"],
        write=True,
    ),
    _tool(
        "napple_certificate_create",
        "Create a signing certificate from a certificate signing request.",
        {
            "certificate_type": _str("For example DISTRIBUTION or IOS_DEVELOPMENT."),
            "csr_content": _str("The PEM text of the certificate signing request."),
        },
        ["certificate_type", "csr_content"],
        write=True,
    ),
    _tool(
        "napple_profile_create",
        "Create a provisioning profile for one bundle id.",
        {
            "name": _str("The profile name."),
            "profile_type": _str("For example IOS_APP_STORE."),
            "bundle_id_resource": _str(
                "The bundle id resource id from napple_signing_show, not the identifier."
            ),
            "certificate_ids": _strs("Certificate ids from napple_signing_show."),
            "device_ids": _strs("Device ids. Leave out for store profiles."),
        },
        ["name", "profile_type", "bundle_id_resource", "certificate_ids"],
        write=True,
    ),
]


class Tools:
    """Runs one tool call. An unknown name fails before the client exists."""

    def __init__(
        self, client_factory: Callable[[], Client], upload_root: Path | None = None
    ) -> None:
        self._factory = client_factory
        # A NOTT node's workspace: napple_build_upload reads only inside it.
        self.upload_root = upload_root
        self._client: Client | None = None
        self._lock = threading.Lock()

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        match name:
            case "napple_account_show":
                return _account_show(self._api(), arguments)
            case "napple_app_list":
                return _app_list(self._api(), arguments)
            case "napple_build_list":
                return _build_list(self._api(), arguments)
            case "napple_build_show":
                return _build_show(self._api(), arguments)
            case "napple_signing_show":
                return _signing_show(self._api(), arguments)
            case "napple_group_list":
                return _group_list(self._api(), arguments)
            case "napple_build_upload":
                return _build_upload(self._api(), arguments, self.upload_root)
            case "napple_bundle_create":
                return _bundle_create(self._api(), arguments)
            case "napple_certificate_create":
                return _certificate_create(self._api(), arguments)
            case "napple_profile_create":
                return _profile_create(self._api(), arguments)
            case _:
                raise ValueError(f"unknown tool: {name}")

    def _api(self) -> Client:
        # The lock makes two first calls from worker threads build one client.
        with self._lock:
            if self._client is None:
                self._client = self._factory()
            return self._client


def _opt(args: dict[str, Any], name: str) -> str:
    """An optional string argument. Empty when the caller left it out."""
    value = args.get(name) or ""
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    return value


def _text(args: dict[str, Any], name: str) -> str:
    value = _opt(args, name)
    if not value:
        raise ValueError(f"missing argument: {name}")
    return value


def _texts(args: dict[str, Any], name: str) -> list[str]:
    value = args.get(name) or []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise TypeError(f"{name} must be a list of strings")
    return value


def _limit(args: dict[str, Any]) -> int:
    limit = args.get("limit", 20)
    if not isinstance(limit, int) or not 1 <= limit <= 200:
        raise ValueError("limit must be a whole number from 1 to 200")
    return limit


def _pick(resource: dict[str, Any], *names: str) -> dict[str, Any]:
    """The id and the named attributes of one resource. A missing attribute is None."""
    attrs = resource.get("attributes", {})
    return {"id": resource["id"], **{name: attrs.get(name) for name in names}}


def _included(doc: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    """The `included` resources of a JSON:API document, keyed by type and id."""
    return {(item["type"], item["id"]): item for item in doc.get("included", [])}


def _build(
    resource: dict[str, Any], included: dict[tuple[str, str], dict[str, Any]]
) -> dict[str, Any]:
    attrs = resource.get("attributes", {})
    ref = resource.get("relationships", {}).get("buildBetaDetail", {}).get("data")
    beta = included.get((ref["type"], ref["id"]), {}) if ref else {}
    beta_attrs = beta.get("attributes", {})
    return {
        "id": resource["id"],
        "number": attrs.get("version"),
        "processingState": attrs.get("processingState"),
        "expired": attrs.get("expired"),
        "uploadedDate": attrs.get("uploadedDate"),
        "usesNonExemptEncryption": attrs.get("usesNonExemptEncryption"),
        "internalBuildState": beta_attrs.get("internalBuildState"),
        "externalBuildState": beta_attrs.get("externalBuildState"),
    }


def _account_show(client: Client, args: dict[str, Any]) -> dict[str, Any]:
    page = client.get("/v1/apps", {"limit": "1"})
    return {"ok": True, "apps_visible": page["meta"]["paging"]["total"]}


def _app_list(client: Client, args: dict[str, Any]) -> dict[str, Any]:
    # shortcut: one page of 200 apps and the newest 50 App Store versions per app. A platform that only an older version has can be missing. Upgrade: follow the links when an app needs more.
    doc = client.get(
        "/v1/apps",
        {
            "include": "appStoreVersions",
            "fields[appStoreVersions]": "platform",
            "limit": "200",
            "limit[appStoreVersions]": str(RELATION_LIMIT),
        },
    )
    included = _included(doc)
    return {"apps": [_app(app, included) for app in doc["data"]]}


def _app(
    app: dict[str, Any], included: dict[tuple[str, str], dict[str, Any]]
) -> dict[str, Any]:
    versions = _related(app, "appStoreVersions", included)
    platforms = sorted({version["attributes"]["platform"] for version in versions})
    return {
        **_pick(app, "bundleId", "name", "sku"),
        "platforms": platforms,
        "platforms_at_limit": _at_limit(app, "appStoreVersions"),
    }


def _build_list(client: Client, args: dict[str, Any]) -> dict[str, Any]:
    params = {
        "filter[app]": _text(args, "app_id"),
        "sort": "-version",
        "include": "buildBetaDetail",
        "limit": str(_limit(args)),
    }
    if version := _opt(args, "version"):
        params["filter[preReleaseVersion.version]"] = version
    if platform := _opt(args, "platform"):
        params["filter[preReleaseVersion.platform]"] = platform
    doc = client.get("/v1/builds", params)
    included = _included(doc)
    return {"builds": [_build(build, included) for build in doc["data"]]}


def _build_show(client: Client, args: dict[str, Any]) -> dict[str, Any]:
    build_id = _text(args, "build_id")
    path = f"/v1/builds/{quote(build_id, safe='')}"
    doc = client.get(path, {"include": "buildBetaDetail"})
    return _build(doc["data"], _included(doc))


def _signing_show(client: Client, args: dict[str, Any]) -> dict[str, Any]:
    identifier = _text(args, "bundle_id")
    doc = client.get(
        "/v1/bundleIds",
        {
            "filter[identifier]": identifier,
            "include": "bundleIdCapabilities,profiles",
            "limit": "200",
            "limit[profiles]": str(RELATION_LIMIT),
            "limit[bundleIdCapabilities]": str(RELATION_LIMIT),
        },
    )
    # Apple matches the identifier by prefix, so only an exact match is the bundle.
    matches = [
        item
        for item in doc["data"]
        if item.get("attributes", {}).get("identifier") == identifier
    ]
    if not matches:
        raise NotFoundError(f"no bundle id with identifier {identifier}")
    bundle = matches[0]
    included = _included(doc)
    certificates = client.get_all("/v1/certificates")
    return {
        "bundle": _pick(bundle, "identifier", "name", "platform"),
        "capabilities": [
            item["attributes"]["capabilityType"]
            for item in _related(bundle, "bundleIdCapabilities", included)
        ],
        "profiles": [
            _pick(item, "name", "profileType", "profileState", "expirationDate", "uuid")
            for item in _related(bundle, "profiles", included)
        ],
        "profiles_at_limit": _at_limit(bundle, "profiles"),
        "capabilities_at_limit": _at_limit(bundle, "bundleIdCapabilities"),
        "certificates": [
            _pick(
                cert, "certificateType", "displayName", "expirationDate", "serialNumber"
            )
            for cert in certificates
        ],
    }


def _group_list(client: Client, args: dict[str, Any]) -> dict[str, Any]:
    # shortcut: at most 50 testers and 50 builds per group, and no paging into them. Upgrade: follow the relationship links when a group needs more.
    doc = client.get(
        "/v1/betaGroups",
        {
            "filter[app]": _text(args, "app_id"),
            "include": "builds,betaTesters",
            "limit": "200",
            "limit[builds]": str(RELATION_LIMIT),
            "limit[betaTesters]": str(RELATION_LIMIT),
        },
    )
    included = _included(doc)
    return {"groups": [_group(group, included) for group in doc["data"]]}


def _group(
    group: dict[str, Any], included: dict[tuple[str, str], dict[str, Any]]
) -> dict[str, Any]:
    testers = _related(group, "betaTesters", included)
    builds = _related(group, "builds", included)
    return {
        **_pick(group, "name", "isInternalGroup", "publicLinkEnabled"),
        "testers": [
            _pick(tester, "email", "firstName", "lastName", "state")
            for tester in testers
        ],
        "builds": [_pick(build, "version") for build in builds],
        "testers_at_limit": _at_limit(group, "betaTesters"),
        "builds_at_limit": _at_limit(group, "builds"),
    }


def _refs(resource: dict[str, Any], name: str) -> list[dict[str, Any]]:
    return resource.get("relationships", {}).get(name, {}).get("data") or []


def _related(
    resource: dict[str, Any],
    name: str,
    included: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    return [included[(ref["type"], ref["id"])] for ref in _refs(resource, name)]


def _at_limit(resource: dict[str, Any], name: str) -> bool:
    """True when the relation came back at RELATION_LIMIT, so more may exist."""
    return len(_refs(resource, name)) >= RELATION_LIMIT


def _build_upload(
    client: Client, args: dict[str, Any], upload_root: Path | None
) -> dict[str, Any]:
    return upload_build(
        client,
        Path(_text(args, "path")),
        app_id=_text(args, "app_id"),
        version=_text(args, "version"),
        build_number=_text(args, "build_number"),
        platform=_text(args, "platform"),
        upload_root=upload_root,
    )


def _bundle_create(client: Client, args: dict[str, Any]) -> dict[str, Any]:
    attributes = {
        "identifier": _text(args, "identifier"),
        "name": _text(args, "name"),
        "platform": _text(args, "platform"),
    }
    bundle = client.post(
        "/v1/bundleIds", {"data": {"type": "bundleIds", "attributes": attributes}}
    )["data"]
    for capability in _texts(args, "capabilities"):
        client.post(
            "/v1/bundleIdCapabilities",
            {
                "data": {
                    "type": "bundleIdCapabilities",
                    "attributes": {"capabilityType": capability},
                    "relationships": {
                        "bundleId": {"data": {"type": "bundleIds", "id": bundle["id"]}}
                    },
                }
            },
        )
    return _pick(bundle, "identifier", "name", "platform")


def _certificate_create(client: Client, args: dict[str, Any]) -> dict[str, Any]:
    attributes = {
        "certificateType": _text(args, "certificate_type"),
        "csrContent": _text(args, "csr_content"),
    }
    cert = client.post(
        "/v1/certificates", {"data": {"type": "certificates", "attributes": attributes}}
    )["data"]
    return _pick(
        cert, "certificateType", "expirationDate", "serialNumber", "certificateContent"
    )


def _profile_create(client: Client, args: dict[str, Any]) -> dict[str, Any]:
    certificate_ids = _texts(args, "certificate_ids")
    if not certificate_ids:
        raise ValueError("missing argument: certificate_ids")
    relationships: dict[str, Any] = {
        "bundleId": {
            "data": {"type": "bundleIds", "id": _text(args, "bundle_id_resource")}
        },
        "certificates": {
            "data": [{"type": "certificates", "id": cert} for cert in certificate_ids]
        },
    }
    device_ids = _texts(args, "device_ids")
    if device_ids:
        relationships["devices"] = {
            "data": [{"type": "devices", "id": device} for device in device_ids]
        }
    attributes = {
        "name": _text(args, "name"),
        "profileType": _text(args, "profile_type"),
    }
    body = {
        "data": {
            "type": "profiles",
            "attributes": attributes,
            "relationships": relationships,
        }
    }
    profile = client.post("/v1/profiles", body)["data"]
    return _pick(profile, "uuid", "profileType", "expirationDate", "profileContent")
