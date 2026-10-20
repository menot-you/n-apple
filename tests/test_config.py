"""The configuration: the three environment names, else the key directory."""

from __future__ import annotations

from pathlib import Path

import pytest

from napple.config import ISSUER_ID, KEY_ID, KEY_PATH, load
from napple.errors import ConfigError


def key_directory(
    tmp_path: Path, *key_ids: str, issuer: str | None = "issuer-uuid"
) -> Path:
    directory = tmp_path / ".nott" / "apple"
    directory.mkdir(parents=True)
    for key_id in key_ids:
        (directory / f"AuthKey_{key_id}.p8").write_text("key")
    if issuer is not None:
        (directory / "issuer_id").write_text(f"{issuer}\n")
    return directory


def test_the_environment_wins_over_the_directory(tmp_path: Path) -> None:
    key_directory(tmp_path, "DIRKEY")
    env = {
        KEY_ID: "ENVKEY",
        ISSUER_ID: "env-issuer",
        KEY_PATH: "/k.p8",
        "HOME": str(tmp_path),
    }

    config = load(env)

    assert (config.key_id, config.issuer_id, config.key_path) == (
        "ENVKEY",
        "env-issuer",
        Path("/k.p8"),
    )


def test_no_names_set_reads_the_key_directory(tmp_path: Path) -> None:
    directory = key_directory(tmp_path, "ABC123")

    config = load({"HOME": str(tmp_path)})

    assert config.key_id == "ABC123"
    assert config.issuer_id == "issuer-uuid"
    assert config.key_path == directory / "AuthKey_ABC123.p8"


def test_a_partial_environment_is_refused_and_names_the_gap(tmp_path: Path) -> None:
    key_directory(tmp_path, "ABC123")

    with pytest.raises(ConfigError, match=f"not set: {ISSUER_ID}, {KEY_PATH}"):
        load({KEY_ID: "K", "HOME": str(tmp_path)})


@pytest.mark.parametrize(
    ("key_ids", "issuer", "reason"),
    [
        ((), "issuer-uuid", "no AuthKey_<id>.p8"),
        (("A", "B"), "issuer-uuid", "2 AuthKey_<id>.p8"),
        (("A",), None, "issuer_id"),
        (("A",), "  ", "issuer_id"),
    ],
)
def test_an_incomplete_directory_is_refused(
    tmp_path: Path, key_ids: tuple[str, ...], issuer: str | None, reason: str
) -> None:
    key_directory(tmp_path, *key_ids, issuer=issuer)

    with pytest.raises(ConfigError, match=f"not set: {KEY_ID}") as caught:
        load({"HOME": str(tmp_path)})

    assert reason in str(caught.value)


def test_a_given_environment_without_home_never_reaches_the_real_home() -> None:
    with pytest.raises(ConfigError, match="HOME"):
        load({})


def test_an_empty_key_id_in_the_file_name_is_refused(tmp_path: Path) -> None:
    key_directory(tmp_path, "")

    with pytest.raises(ConfigError, match="empty key id"):
        load({"HOME": str(tmp_path)})


def test_an_issuer_id_that_is_not_utf8_is_a_config_error(tmp_path: Path) -> None:
    directory = key_directory(tmp_path, "A", issuer=None)
    (directory / "issuer_id").write_bytes(b"\xff\xfe")

    with pytest.raises(ConfigError, match="issuer_id"):
        load({"HOME": str(tmp_path)})


def test_a_key_file_error_names_the_key_path(tmp_path: Path) -> None:
    key_directory(tmp_path, "A")
    config = load({"HOME": str(tmp_path)})
    config.key_path.chmod(0o644)

    with pytest.raises(ConfigError) as caught:
        config.private_key()

    assert str(config.key_path) in str(caught.value)
    assert KEY_PATH not in str(caught.value)


def test_a_directory_named_like_a_key_is_not_a_key(tmp_path: Path) -> None:
    directory = key_directory(tmp_path)
    (directory / "AuthKey_X.p8").mkdir()

    with pytest.raises(ConfigError, match="no AuthKey_<id>.p8"):
        load({"HOME": str(tmp_path)})


def test_an_unreadable_key_file_is_a_config_error(tmp_path: Path) -> None:
    key_directory(tmp_path, "A")
    config = load({"HOME": str(tmp_path)})
    config.key_path.chmod(0o000)

    with pytest.raises(ConfigError, match="cannot read the key file"):
        config.private_key()
