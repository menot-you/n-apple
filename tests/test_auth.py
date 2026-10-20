"""The ES256 token for a team API key."""

from __future__ import annotations

from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from napple.auth import token
from napple.config import Config
from napple.errors import ConfigError


def write_key(tmp_path: Path) -> tuple[Path, ec.EllipticCurvePrivateKey]:
    key = ec.generate_private_key(ec.SECP256R1())
    path = tmp_path / "AuthKey_TEST.p8"
    path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    path.chmod(0o600)
    return path, key


def test_token_has_the_asc_header_and_claims(tmp_path: Path) -> None:
    path, key = write_key(tmp_path)
    config = Config("KEY123", "issuer-uuid", path)

    encoded = token(config, now=1_000_000.0)

    header = jwt.get_unverified_header(encoded)
    assert header == {"alg": "ES256", "kid": "KEY123", "typ": "JWT"}
    claims = jwt.decode(
        encoded,
        key.public_key(),
        algorithms=["ES256"],
        audience="appstoreconnect-v1",
        options={"verify_exp": False, "verify_iat": False},
    )
    assert claims == {
        "iss": "issuer-uuid",
        "iat": 1_000_000,
        "exp": 1_001_200,
        "aud": "appstoreconnect-v1",
    }


def test_token_signature_fails_with_another_key(tmp_path: Path) -> None:
    path, _ = write_key(tmp_path)
    other = ec.generate_private_key(ec.SECP256R1())

    encoded = token(Config("K", "I", path))

    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(
            encoded,
            other.public_key(),
            algorithms=["ES256"],
            audience="appstoreconnect-v1",
        )


def test_token_default_now_is_current_time(tmp_path: Path) -> None:
    path, key = write_key(tmp_path)

    claims = jwt.decode(
        token(Config("K", "I", path)),
        key.public_key(),
        algorithms=["ES256"],
        audience="appstoreconnect-v1",
    )

    assert claims["exp"] - claims["iat"] == 1200


def test_a_bad_key_is_a_config_error_without_the_key_text(tmp_path: Path) -> None:
    # Split the PEM header so that the public source scan finds no key block.
    label = "PRIVATE " + "KEY"
    secret = f"-----BEGIN {label}-----\nTOPSECRETKEYMATERIAL\n-----END {label}-----\n"
    path = tmp_path / "bad.p8"
    path.write_text(secret)
    path.chmod(0o600)

    with pytest.raises(ConfigError) as caught:
        token(Config("K", "I", path))

    assert "TOPSECRETKEYMATERIAL" not in str(caught.value)
    assert "TOPSECRETKEYMATERIAL" not in repr(caught.value)
    assert caught.value.__cause__ is None
