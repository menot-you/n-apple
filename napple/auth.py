"""The ES256 token that App Store Connect accepts for a team API key."""

from __future__ import annotations

import time

import jwt

from .config import Config
from .errors import ConfigError

AUDIENCE = "appstoreconnect-v1"
LIFETIME = 1200  # Apple refuses a token that lives longer than 20 minutes.


def token(config: Config, *, now: float | None = None) -> str:
    """ES256 JWT for a team API key: header kid=key_id, typ=JWT; claims iss=issuer_id, iat, exp=iat+1200, aud='appstoreconnect-v1'."""
    issued = int(time.time() if now is None else now)
    claims = {
        "iss": config.issuer_id,
        "iat": issued,
        "exp": issued + LIFETIME,
        "aud": AUDIENCE,
    }
    key = config.private_key()
    try:
        return jwt.encode(
            claims,
            key,
            algorithm="ES256",
            headers={"kid": config.key_id, "typ": "JWT"},
        )
    except ValueError, TypeError, jwt.PyJWTError:
        # The cause can quote the key text. Do not chain it.
        raise ConfigError(
            "the key file does not hold a usable ES256 private key"
        ) from None
