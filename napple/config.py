"""The API key configuration: three environment names under the member prefix NOTT_NAPPLE_,
else the key directory ~/.nott/apple."""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .errors import ConfigError

KEY_ID = "NOTT_NAPPLE_KEY_ID"
ISSUER_ID = "NOTT_NAPPLE_ISSUER_ID"
KEY_PATH = "NOTT_NAPPLE_KEY_PATH"
API_ROOT = "https://api.appstoreconnect.apple.com"
KEY_DIRECTORY = Path(".nott/apple")


@dataclass(frozen=True)
class Config:
    key_id: str
    issuer_id: str
    key_path: Path
    api_root: str = API_ROOT

    def private_key(self) -> str:
        """Read the .p8 key. Refuse a key file that other users can read."""
        try:
            mode = self.key_path.stat().st_mode
        except OSError as error:
            raise ConfigError(
                f"{self.key_path}: cannot read the key file: {error.strerror}"
            ) from error
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise ConfigError(f"{self.key_path}: the key file must have mode 600")
        try:
            return self.key_path.read_text()
        except OSError as error:
            raise ConfigError(
                f"{self.key_path}: cannot read the key file: {error.strerror}"
            ) from None


def load(environ: Mapping[str, str] | None = None) -> Config:
    """Read the configuration. A missing name is a ConfigError that names it.

    When none of the three names is set, read the key directory instead: one
    AuthKey_<key id>.p8 file and an issuer_id file.
    """
    env = os.environ if environ is None else environ
    names = (KEY_ID, ISSUER_ID, KEY_PATH)
    missing = [name for name in names if not env.get(name)]
    if len(missing) == len(names):
        if env.get("HOME"):
            home = Path(env["HOME"])
        elif environ is None:
            home = Path.home()
        else:
            # A given environment (a test) never falls through to the real home.
            raise ConfigError(f"not set: {', '.join(missing)}, and HOME is not set")
        return _from_directory(home / KEY_DIRECTORY, ", ".join(missing))
    if missing:
        raise ConfigError(f"not set: {', '.join(missing)}")
    return Config(env[KEY_ID], env[ISSUER_ID], Path(env[KEY_PATH]).expanduser())


def _from_directory(directory: Path, missing: str) -> Config:
    keys = sorted(path for path in directory.glob("AuthKey_*.p8") if path.is_file())
    if len(keys) != 1:
        count = len(keys) or "no"
        raise ConfigError(
            f"not set: {missing}, and {directory} holds {count} AuthKey_<id>.p8"
        )
    key_id = keys[0].stem.removeprefix("AuthKey_")
    if not key_id:
        raise ConfigError(f"not set: {missing}, and {keys[0]} has an empty key id")
    try:
        issuer = (directory / "issuer_id").read_text(encoding="utf-8").strip()
    except OSError as error:
        raise ConfigError(
            f"not set: {missing}, and {directory}/issuer_id: {error.strerror}"
        ) from None
    except UnicodeDecodeError:
        raise ConfigError(
            f"not set: {missing}, and {directory}/issuer_id is not UTF-8 text"
        ) from None
    if not issuer:
        raise ConfigError(f"not set: {missing}, and {directory}/issuer_id is empty")
    return Config(key_id, issuer, keys[0])
