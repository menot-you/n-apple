"""Tests for the ten napple tools: the request each one sends, and the result it returns.

FakeClient stands in for the App Store Connect client. It records every call and
answers from canned JSON:API documents, so no HTTP runs here.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from napple.errors import AgreementError, NotFoundError
from napple.server import TOOLS, Tools

ALL_TOOLS = [
    "napple_account_show",
    "napple_app_list",
    "napple_build_list",
    "napple_build_show",
    "napple_signing_show",
    "napple_group_list",
    "napple_build_upload",
    "napple_bundle_create",
    "napple_certificate_create",
    "napple_profile_create",
]
WRITE_TOOLS = {
    "napple_build_upload",
    "napple_bundle_create",
    "napple_certificate_create",
    "napple_profile_create",
}

BUILD_DOC = {
    "data": [
        {
            "type": "builds",
            "id": "b-1",
            "attributes": {
                "version": "42",
                "processingState": "VALID",
                "expired": False,
                "uploadedDate": "2026-10-01T10:00:00Z",
                "usesNonExemptEncryption": False,
            },
            "relationships": {
                "buildBetaDetail": {"data": {"type": "buildBetaDetails", "id": "bd-1"}}
            },
        }
    ],
    "included": [
        {
            "type": "buildBetaDetails",
            "id": "bd-1",
            "attributes": {
                "internalBuildState": "IN_BETA_TESTING",
                "externalBuildState": "READY_FOR_BETA_TESTING",
            },
        }
    ],
}
BUILD_ROW = {
    "id": "b-1",
    "number": "42",
    "processingState": "VALID",
    "expired": False,
    "uploadedDate": "2026-10-01T10:00:00Z",
    "usesNonExemptEncryption": False,
    "internalBuildState": "IN_BETA_TESTING",
    "externalBuildState": "READY_FOR_BETA_TESTING",
}

BUNDLE_DOC = {
    "data": [
        {
            "type": "bundleIds",
            "id": "bid-1",
            "attributes": {
                "identifier": "com.example.app",
                "name": "Example",
                "platform": "IOS",
            },
            "relationships": {
                "bundleIdCapabilities": {
                    "data": [{"type": "bundleIdCapabilities", "id": "cap-1"}]
                },
                "profiles": {"data": [{"type": "profiles", "id": "prof-1"}]},
            },
        }
    ],
    "included": [
        {
            "type": "bundleIdCapabilities",
            "id": "cap-1",
            "attributes": {"capabilityType": "PUSH_NOTIFICATIONS", "settings": []},
        },
        {
            "type": "profiles",
            "id": "prof-1",
            "attributes": {
                "name": "Example Store",
                "platform": "IOS",
                "profileType": "IOS_APP_STORE",
                "profileState": "ACTIVE",
                "expirationDate": "2027-10-01T00:00:00.000+0000",
                "uuid": "uuid-1",
                "profileContent": "PROFILE-SECRET",
            },
        },
    ],
}
CERTIFICATES = [
    {
        "type": "certificates",
        "id": "cert-1",
        "attributes": {
            "certificateType": "DISTRIBUTION",
            "displayName": "Apple Distribution: Example",
            "expirationDate": "2027-09-01T00:00:00.000+0000",
            "serialNumber": "1A2B",
            "certificateContent": "CERT-SECRET",
        },
    }
]


class FakeClient:
    """Records (method, path, params, body) and answers from a canned table."""

    def __init__(self, answers: dict[tuple[str, str], Any] | None = None) -> None:
        self.answers = answers or {}
        self.calls: list[tuple[str, str, Any, Any]] = []

    def _answer(self, method: str, path: str) -> Any:
        return self.answers[(method, path)]

    def get(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        self.calls.append(("get", path, params, None))
        return self._answer("get", path)

    def get_all(
        self, path: str, params: dict[str, str] | None = None, *, limit: int = 200
    ) -> list[dict[str, Any]]:
        self.calls.append(("get_all", path, params, None))
        return self._answer("get_all", path)

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("post", path, None, body))
        return self._answer("post", path)


def run(
    name: str,
    arguments: dict[str, Any],
    answers: dict[tuple[str, str], Any] | None = None,
) -> tuple[dict[str, Any], FakeClient]:
    client = FakeClient(answers)
    return Tools(lambda: client).call(name, arguments), client


def test_tool_list_names_exactly_the_ten_tools() -> None:
    assert [tool["name"] for tool in TOOLS] == ALL_TOOLS


def test_read_tools_are_read_only_and_write_tools_ask_first() -> None:
    for tool in TOOLS:
        if tool["name"] in WRITE_TOOLS:
            assert tool["annotations"] == {
                "readOnlyHint": False,
                "destructiveHint": True,
            }
            assert tool["description"].endswith(
                "Creates or uploads a real Apple asset. Ask the owner before each call."
            )
        else:
            assert tool["annotations"] == {"readOnlyHint": True}


def test_unknown_tool_raises() -> None:
    with pytest.raises(ValueError, match="unknown tool: napple_nope"):
        run("napple_nope", {})


def test_client_is_built_lazily_on_the_first_known_call() -> None:
    built: list[FakeClient] = []

    def factory() -> FakeClient:
        built.append(
            FakeClient({("get", "/v1/apps"): {"meta": {"paging": {"total": 0}}}})
        )
        return built[-1]

    tools = Tools(factory)
    assert built == []
    with pytest.raises(ValueError):
        tools.call("napple_nope", {})
    assert built == []
    tools.call("napple_account_show", {})
    tools.call("napple_account_show", {})
    assert len(built) == 1


def test_missing_argument_names_the_argument() -> None:
    with pytest.raises(ValueError, match="missing argument: build_id"):
        run("napple_build_show", {})


def test_account_show_counts_the_visible_apps() -> None:
    result, client = run(
        "napple_account_show",
        {},
        {
            ("get", "/v1/apps"): {
                "data": [],
                "meta": {"paging": {"total": 3, "limit": 1}},
            }
        },
    )
    assert result == {"ok": True, "apps_visible": 3}
    assert client.calls == [("get", "/v1/apps", {"limit": "1"}, None)]


def test_account_show_propagates_an_agreement_error() -> None:
    class RefusingClient(FakeClient):
        def get(
            self, path: str, params: dict[str, str] | None = None
        ) -> dict[str, Any]:
            raise AgreementError("agreement missing", status=403)

    with pytest.raises(AgreementError):
        Tools(lambda: RefusingClient()).call("napple_account_show", {})


def test_app_list_returns_id_bundle_id_name_sku_and_sorted_platforms() -> None:
    doc = {
        "data": [
            {
                "type": "apps",
                "id": "app-1",
                "attributes": {
                    "bundleId": "com.example.app",
                    "name": "Example",
                    "sku": "EX1",
                },
                "relationships": {
                    "appStoreVersions": {
                        "data": [
                            {"type": "appStoreVersions", "id": "v-1"},
                            {"type": "appStoreVersions", "id": "v-2"},
                            {"type": "appStoreVersions", "id": "v-3"},
                        ]
                    }
                },
            },
            {
                "type": "apps",
                "id": "app-2",
                "attributes": {
                    "bundleId": "com.example.none",
                    "name": "None",
                    "sku": "N1",
                },
                "relationships": {"appStoreVersions": {"data": []}},
            },
        ],
        "included": [
            {
                "type": "appStoreVersions",
                "id": "v-1",
                "attributes": {"platform": "MAC_OS"},
            },
            {
                "type": "appStoreVersions",
                "id": "v-2",
                "attributes": {"platform": "IOS"},
            },
            {
                "type": "appStoreVersions",
                "id": "v-3",
                "attributes": {"platform": "IOS"},
            },
        ],
    }
    result, client = run("napple_app_list", {}, {("get", "/v1/apps"): doc})
    assert client.calls == [
        (
            "get",
            "/v1/apps",
            {
                "include": "appStoreVersions",
                "fields[appStoreVersions]": "platform",
                "limit": "200",
                "limit[appStoreVersions]": "50",
            },
            None,
        )
    ]
    assert result == {
        "apps": [
            {
                "id": "app-1",
                "bundleId": "com.example.app",
                "name": "Example",
                "sku": "EX1",
                "platforms": ["IOS", "MAC_OS"],
                "platforms_at_limit": False,
            },
            {
                "id": "app-2",
                "bundleId": "com.example.none",
                "name": "None",
                "sku": "N1",
                "platforms": [],
                "platforms_at_limit": False,
            },
        ]
    }


def test_app_list_says_when_the_versions_reached_the_limit() -> None:
    versions = [{"type": "appStoreVersions", "id": f"v-{n}"} for n in range(50)]
    doc = {
        "data": [
            {
                "type": "apps",
                "id": "app-3",
                "attributes": {
                    "bundleId": "com.example.big",
                    "name": "Big",
                    "sku": "B1",
                },
                "relationships": {"appStoreVersions": {"data": versions}},
            }
        ],
        "included": [
            {
                "type": "appStoreVersions",
                "id": f"v-{n}",
                "attributes": {"platform": "IOS"},
            }
            for n in range(50)
        ],
    }
    result, _ = run("napple_app_list", {}, {("get", "/v1/apps"): doc})
    assert result["apps"][0]["platforms_at_limit"] is True


def test_build_list_sends_the_filters_sort_and_beta_include() -> None:
    result, client = run(
        "napple_build_list",
        {"app_id": "app-1", "version": "1.2.0", "platform": "IOS", "limit": 5},
        {("get", "/v1/builds"): BUILD_DOC},
    )
    assert client.calls == [
        (
            "get",
            "/v1/builds",
            {
                "filter[app]": "app-1",
                "sort": "-version",
                "include": "buildBetaDetail",
                "limit": "5",
                "filter[preReleaseVersion.version]": "1.2.0",
                "filter[preReleaseVersion.platform]": "IOS",
            },
            None,
        )
    ]
    assert result == {"builds": [BUILD_ROW]}


def test_build_list_defaults_to_twenty_builds_without_version_filters() -> None:
    _, client = run(
        "napple_build_list", {"app_id": "app-1"}, {("get", "/v1/builds"): BUILD_DOC}
    )
    assert client.calls == [
        (
            "get",
            "/v1/builds",
            {
                "filter[app]": "app-1",
                "sort": "-version",
                "include": "buildBetaDetail",
                "limit": "20",
            },
            None,
        )
    ]


def test_build_show_reads_one_build_with_its_beta_state() -> None:
    doc = {"data": BUILD_DOC["data"][0], "included": BUILD_DOC["included"]}
    result, client = run(
        "napple_build_show", {"build_id": "b-1"}, {("get", "/v1/builds/b-1"): doc}
    )
    assert result == BUILD_ROW
    assert client.calls == [
        ("get", "/v1/builds/b-1", {"include": "buildBetaDetail"}, None)
    ]


def test_build_show_quotes_the_id_in_the_path() -> None:
    doc = {"data": BUILD_DOC["data"][0], "included": []}
    _, client = run(
        "napple_build_show", {"build_id": "a/b"}, {("get", "/v1/builds/a%2Fb"): doc}
    )
    assert client.calls[0][1] == "/v1/builds/a%2Fb"


def test_signing_show_returns_bundle_capabilities_profiles_and_certificates() -> None:
    result, client = run(
        "napple_signing_show",
        {"bundle_id": "com.example.app"},
        {
            ("get", "/v1/bundleIds"): BUNDLE_DOC,
            ("get_all", "/v1/certificates"): CERTIFICATES,
        },
    )
    assert client.calls == [
        (
            "get",
            "/v1/bundleIds",
            {
                "filter[identifier]": "com.example.app",
                "include": "bundleIdCapabilities,profiles",
                "limit": "200",
                "limit[profiles]": "50",
                "limit[bundleIdCapabilities]": "50",
            },
            None,
        ),
        ("get_all", "/v1/certificates", None, None),
    ]
    assert result == {
        "bundle": {
            "id": "bid-1",
            "identifier": "com.example.app",
            "name": "Example",
            "platform": "IOS",
        },
        "capabilities": ["PUSH_NOTIFICATIONS"],
        "profiles": [
            {
                "id": "prof-1",
                "name": "Example Store",
                "profileType": "IOS_APP_STORE",
                "profileState": "ACTIVE",
                "expirationDate": "2027-10-01T00:00:00.000+0000",
                "uuid": "uuid-1",
            }
        ],
        "profiles_at_limit": False,
        "capabilities_at_limit": False,
        "certificates": [
            {
                "id": "cert-1",
                "certificateType": "DISTRIBUTION",
                "displayName": "Apple Distribution: Example",
                "expirationDate": "2027-09-01T00:00:00.000+0000",
                "serialNumber": "1A2B",
            }
        ],
    }
    assert "certificateContent" not in json.dumps(result)
    assert "profileContent" not in json.dumps(result)


def test_signing_show_says_when_the_profiles_reached_the_limit() -> None:
    profile_refs = [{"type": "profiles", "id": f"p-{n}"} for n in range(50)]
    doc = {
        "data": [
            {
                "type": "bundleIds",
                "id": "bid-9",
                "attributes": {
                    "identifier": "com.example.many",
                    "name": "Many",
                    "platform": "IOS",
                },
                "relationships": {
                    "bundleIdCapabilities": {"data": []},
                    "profiles": {"data": profile_refs},
                },
            }
        ],
        "included": [
            {
                "type": "profiles",
                "id": f"p-{n}",
                "attributes": {"name": f"P{n}", "uuid": f"u-{n}"},
            }
            for n in range(50)
        ],
    }
    result, _ = run(
        "napple_signing_show",
        {"bundle_id": "com.example.many"},
        {("get", "/v1/bundleIds"): doc, ("get_all", "/v1/certificates"): []},
    )
    assert result["profiles_at_limit"] is True
    assert result["capabilities_at_limit"] is False


# Shaped from the live proof A5b: Apple's filter[identifier] matches by prefix, so
# "tt.no.app" also returns "tt.no.app.widgets", and that item comes first.
PREFIX_MATCH_DOC = {
    "data": [
        {
            "type": "bundleIds",
            "id": "NCUS622G22",
            "attributes": {
                "identifier": "tt.no.app.widgets",
                "name": "Widgets",
                "platform": "IOS",
            },
            "relationships": {
                "bundleIdCapabilities": {
                    "data": [
                        {
                            "type": "bundleIdCapabilities",
                            "id": "NCUS622G22_IN_APP_PURCHASE",
                        }
                    ]
                },
                "profiles": {"data": [{"type": "profiles", "id": "WIDGET-PROFILE"}]},
            },
        },
        {
            "type": "bundleIds",
            "id": "QKCXNNF37B",
            "attributes": {
                "identifier": "tt.no.app",
                "name": "No App",
                "platform": "IOS",
            },
            "relationships": {
                "bundleIdCapabilities": {
                    "data": [
                        {
                            "type": "bundleIdCapabilities",
                            "id": "QKCXNNF37B_IN_APP_PURCHASE",
                        }
                    ]
                },
                "profiles": {"data": [{"type": "profiles", "id": "YQ22MJ7357"}]},
            },
        },
    ],
    "included": [
        {
            "type": "bundleIdCapabilities",
            "id": "NCUS622G22_IN_APP_PURCHASE",
            "attributes": {"capabilityType": "IN_APP_PURCHASE"},
        },
        {
            "type": "bundleIdCapabilities",
            "id": "QKCXNNF37B_IN_APP_PURCHASE",
            "attributes": {"capabilityType": "IN_APP_PURCHASE"},
        },
        {
            "type": "profiles",
            "id": "WIDGET-PROFILE",
            "attributes": {
                "name": "Widget Store",
                "profileType": "IOS_APP_STORE",
                "profileState": "ACTIVE",
                "expirationDate": "2027-10-01T00:00:00.000+0000",
                "uuid": "uuid-widget",
                "profileContent": "WIDGET-SECRET",
            },
        },
        {
            "type": "profiles",
            "id": "YQ22MJ7357",
            "attributes": {
                "name": "No App Store",
                "profileType": "IOS_APP_STORE",
                "profileState": "ACTIVE",
                "expirationDate": "2027-10-02T00:00:00.000+0000",
                "uuid": "uuid-app",
                "profileContent": "APP-SECRET",
            },
        },
    ],
}


def test_signing_show_takes_the_exact_identifier_not_the_prefix_match() -> None:
    result, client = run(
        "napple_signing_show",
        {"bundle_id": "tt.no.app"},
        {
            ("get", "/v1/bundleIds"): PREFIX_MATCH_DOC,
            ("get_all", "/v1/certificates"): [],
        },
    )
    assert result["bundle"] == {
        "id": "QKCXNNF37B",
        "identifier": "tt.no.app",
        "name": "No App",
        "platform": "IOS",
    }
    assert result["capabilities"] == ["IN_APP_PURCHASE"]
    assert [profile["id"] for profile in result["profiles"]] == ["YQ22MJ7357"]
    assert "WIDGET-SECRET" not in json.dumps(result)
    assert client.calls[0][2] == {
        "filter[identifier]": "tt.no.app",
        "include": "bundleIdCapabilities,profiles",
        "limit": "200",
        "limit[profiles]": "50",
        "limit[bundleIdCapabilities]": "50",
    }


def test_signing_show_refuses_when_only_a_prefix_matches() -> None:
    only_prefix = {"data": PREFIX_MATCH_DOC["data"][:1], "included": []}
    with pytest.raises(NotFoundError, match="tt.no.app"):
        run(
            "napple_signing_show",
            {"bundle_id": "tt.no.app"},
            {
                ("get", "/v1/bundleIds"): only_prefix,
                ("get_all", "/v1/certificates"): [],
            },
        )


def test_signing_show_for_an_unknown_identifier_is_not_found() -> None:
    with pytest.raises(NotFoundError, match="com.example.none"):
        run(
            "napple_signing_show",
            {"bundle_id": "com.example.none"},
            {
                ("get", "/v1/bundleIds"): {"data": []},
                ("get_all", "/v1/certificates"): [],
            },
        )


def test_group_list_returns_each_group_with_its_testers_and_builds() -> None:
    doc = {
        "data": [
            {
                "type": "betaGroups",
                "id": "g-1",
                "attributes": {
                    "name": "Internal",
                    "isInternalGroup": True,
                    "publicLinkEnabled": False,
                },
                "relationships": {
                    "betaTesters": {"data": [{"type": "betaTesters", "id": "t-1"}]},
                    "builds": {"data": [{"type": "builds", "id": "b-1"}]},
                },
            }
        ],
        "included": [
            {
                "type": "betaTesters",
                "id": "t-1",
                "attributes": {
                    "email": "ana@example.com",
                    "firstName": "Ana",
                    "lastName": "Lima",
                    "state": "ACCEPTED",
                },
            },
            {
                "type": "builds",
                "id": "b-1",
                "attributes": {"version": "42"},
            },
        ],
    }
    result, client = run(
        "napple_group_list",
        {"app_id": "app-1"},
        {("get", "/v1/betaGroups"): doc},
    )
    assert client.calls == [
        (
            "get",
            "/v1/betaGroups",
            {
                "filter[app]": "app-1",
                "include": "builds,betaTesters",
                "limit": "200",
                "limit[builds]": "50",
                "limit[betaTesters]": "50",
            },
            None,
        )
    ]
    assert result == {
        "groups": [
            {
                "id": "g-1",
                "name": "Internal",
                "isInternalGroup": True,
                "publicLinkEnabled": False,
                "testers": [
                    {
                        "id": "t-1",
                        "email": "ana@example.com",
                        "firstName": "Ana",
                        "lastName": "Lima",
                        "state": "ACCEPTED",
                    }
                ],
                "builds": [{"id": "b-1", "version": "42"}],
                "testers_at_limit": False,
                "builds_at_limit": False,
            }
        ]
    }


def test_group_list_says_when_testers_and_builds_reached_the_limit() -> None:
    testers = [{"type": "betaTesters", "id": f"t-{n}"} for n in range(50)]
    builds = [{"type": "builds", "id": f"b-{n}"} for n in range(50)]
    doc = {
        "data": [
            {
                "type": "betaGroups",
                "id": "g-9",
                "attributes": {
                    "name": "Big",
                    "isInternalGroup": False,
                    "publicLinkEnabled": True,
                },
                "relationships": {
                    "betaTesters": {"data": testers},
                    "builds": {"data": builds},
                },
            }
        ],
        "included": [
            {
                "type": "betaTesters",
                "id": f"t-{n}",
                "attributes": {"email": f"t{n}@example.com"},
            }
            for n in range(50)
        ]
        + [
            {"type": "builds", "id": f"b-{n}", "attributes": {"version": str(n)}}
            for n in range(50)
        ],
    }
    result, _ = run(
        "napple_group_list", {"app_id": "app-1"}, {("get", "/v1/betaGroups"): doc}
    )
    group = result["groups"][0]
    assert group["testers_at_limit"] is True
    assert group["builds_at_limit"] is True


def test_build_upload_hands_the_arguments_to_the_upload_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def upload_build(client: Any, path: Path, **options: Any) -> dict[str, str]:
        seen.update(client=client, path=path, **options)
        return {"id": "b-9"}

    monkeypatch.setattr("napple.server.upload_build", upload_build)

    client = FakeClient()
    result = Tools(lambda: client).call(
        "napple_build_upload",
        {
            "path": "/var/tmp/app.ipa",
            "app_id": "app-1",
            "version": "1.2.0",
            "build_number": "42",
            "platform": "IOS",
        },
    )
    assert result == {"id": "b-9"}
    assert seen == {
        "client": client,
        "path": Path("/var/tmp/app.ipa"),
        "app_id": "app-1",
        "version": "1.2.0",
        "build_number": "42",
        "platform": "IOS",
        "upload_root": None,
    }
    seen.clear()
    Tools(lambda: client, upload_root=Path("/var/tmp/workspace")).call(
        "napple_build_upload",
        {
            "path": "app.ipa",
            "app_id": "app-1",
            "version": "1.2.0",
            "build_number": "42",
            "platform": "IOS",
        },
    )
    assert seen["upload_root"] == Path("/var/tmp/workspace")


def test_bundle_create_registers_the_id_then_adds_each_capability() -> None:
    bundle = {
        "type": "bundleIds",
        "id": "bid-2",
        "attributes": {
            "identifier": "com.example.new",
            "name": "New",
            "platform": "IOS",
        },
    }
    result, client = run(
        "napple_bundle_create",
        {
            "identifier": "com.example.new",
            "name": "New",
            "platform": "IOS",
            "capabilities": ["PUSH_NOTIFICATIONS", "APP_GROUPS"],
        },
        {
            ("post", "/v1/bundleIds"): {"data": bundle},
            ("post", "/v1/bundleIdCapabilities"): {
                "data": {"type": "bundleIdCapabilities", "id": "c"}
            },
        },
    )

    def capability(cap: str) -> dict[str, Any]:
        return {
            "data": {
                "type": "bundleIdCapabilities",
                "attributes": {"capabilityType": cap},
                "relationships": {
                    "bundleId": {"data": {"type": "bundleIds", "id": "bid-2"}}
                },
            }
        }

    assert client.calls == [
        (
            "post",
            "/v1/bundleIds",
            None,
            {
                "data": {
                    "type": "bundleIds",
                    "attributes": {
                        "identifier": "com.example.new",
                        "name": "New",
                        "platform": "IOS",
                    },
                }
            },
        ),
        ("post", "/v1/bundleIdCapabilities", None, capability("PUSH_NOTIFICATIONS")),
        ("post", "/v1/bundleIdCapabilities", None, capability("APP_GROUPS")),
    ]
    assert result == {
        "id": "bid-2",
        "identifier": "com.example.new",
        "name": "New",
        "platform": "IOS",
    }


def test_certificate_create_posts_the_csr_and_returns_the_certificate() -> None:
    csr = (
        "-----BEGIN CERTIFICATE REQUEST-----\nMIIB\n-----END CERTIFICATE REQUEST-----\n"
    )
    cert = {
        "type": "certificates",
        "id": "cert-2",
        "attributes": {
            "certificateType": "DISTRIBUTION",
            "expirationDate": "2027-10-19T00:00:00.000+0000",
            "serialNumber": "3C4D",
            "certificateContent": "MIIDdummy",
        },
    }
    result, client = run(
        "napple_certificate_create",
        {"certificate_type": "DISTRIBUTION", "csr_content": csr},
        {("post", "/v1/certificates"): {"data": cert}},
    )
    assert client.calls == [
        (
            "post",
            "/v1/certificates",
            None,
            {
                "data": {
                    "type": "certificates",
                    "attributes": {
                        "certificateType": "DISTRIBUTION",
                        "csrContent": csr,
                    },
                }
            },
        )
    ]
    assert result == {
        "id": "cert-2",
        "certificateType": "DISTRIBUTION",
        "expirationDate": "2027-10-19T00:00:00.000+0000",
        "serialNumber": "3C4D",
        "certificateContent": "MIIDdummy",
    }


PROFILE = {
    "type": "profiles",
    "id": "prof-2",
    "attributes": {
        "uuid": "uuid-2",
        "profileType": "IOS_APP_DEVELOPMENT",
        "expirationDate": "2027-10-19T00:00:00.000+0000",
        "profileContent": "PROFILE-B64",
    },
}


def test_profile_create_links_the_bundle_certificates_and_devices() -> None:
    result, client = run(
        "napple_profile_create",
        {
            "name": "Example Dev",
            "profile_type": "IOS_APP_DEVELOPMENT",
            "bundle_id_resource": "bid-1",
            "certificate_ids": ["cert-1"],
            "device_ids": ["dev-1"],
        },
        {("post", "/v1/profiles"): {"data": PROFILE}},
    )
    assert client.calls == [
        (
            "post",
            "/v1/profiles",
            None,
            {
                "data": {
                    "type": "profiles",
                    "attributes": {
                        "name": "Example Dev",
                        "profileType": "IOS_APP_DEVELOPMENT",
                    },
                    "relationships": {
                        "bundleId": {"data": {"type": "bundleIds", "id": "bid-1"}},
                        "certificates": {
                            "data": [{"type": "certificates", "id": "cert-1"}]
                        },
                        "devices": {"data": [{"type": "devices", "id": "dev-1"}]},
                    },
                }
            },
        )
    ]
    assert result == {
        "id": "prof-2",
        "uuid": "uuid-2",
        "profileType": "IOS_APP_DEVELOPMENT",
        "expirationDate": "2027-10-19T00:00:00.000+0000",
        "profileContent": "PROFILE-B64",
    }


def test_profile_create_without_devices_sends_no_devices_relationship() -> None:
    _, client = run(
        "napple_profile_create",
        {
            "name": "Example Store",
            "profile_type": "IOS_APP_STORE",
            "bundle_id_resource": "bid-1",
            "certificate_ids": ["cert-1"],
        },
        {("post", "/v1/profiles"): {"data": PROFILE}},
    )
    relationships = client.calls[0][3]["data"]["relationships"]
    assert "devices" not in relationships


def test_first_calls_from_threads_build_one_client() -> None:
    built: list[FakeClient] = []

    def factory() -> FakeClient:
        time.sleep(0.05)  # widen the window between two first calls
        built.append(
            FakeClient({("get", "/v1/apps"): {"meta": {"paging": {"total": 1}}}})
        )
        return built[-1]

    tools = Tools(factory)
    threads = [
        threading.Thread(target=tools.call, args=("napple_account_show", {}))
        for _ in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(built) == 1
