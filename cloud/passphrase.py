"""
The upload page's passphrase (AD-17). /calls-log/intake_passphrase_hash holds

    pbkdf2_sha256$<iterations>$<salt, base64>$<hash, base64>

written by tools/put_parameters.py --passphrase, so neither AWS nor the repo
ever holds the passphrase itself. The intake checks every API call against
it: a wrong passphrase costs a full PBKDF2 and is compared in constant time.

Standard library only: the PC's put_parameters.py imports it too.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from typing import Optional

ITERATIONS = 600_000   # OWASP's 2023 figure for PBKDF2-HMAC-SHA256
MIN_LENGTH = 12
_SCHEME = "pbkdf2_sha256"


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _derive(passphrase: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, iterations)


def hash_passphrase(passphrase: str, *, iterations: int = ITERATIONS, salt: Optional[bytes] = None) -> str:
    salt = salt if salt is not None else secrets.token_bytes(16)
    return f"{_SCHEME}${iterations}${_b64(salt)}${_b64(_derive(passphrase, salt, iterations))}"


def verify(passphrase: str, stored: str) -> bool:
    """True when passphrase matches the stored hash. A malformed hash matches nothing."""
    try:
        scheme, iterations, salt, expected = stored.split("$")
        if scheme != _SCHEME:
            return False
        salt_raw, expected_raw = base64.b64decode(salt, validate=True), base64.b64decode(expected, validate=True)
        rounds = int(iterations)
    except ValueError:   # binascii.Error is a ValueError too
        return False
    if rounds < 1 or not salt_raw or not expected_raw:
        return False
    return hmac.compare_digest(_derive(passphrase, salt_raw, rounds), expected_raw)


def weak(passphrase: str) -> Optional[str]:
    """Why a new passphrase is refused, or None."""
    if len(passphrase) < MIN_LENGTH:
        return f"use at least {MIN_LENGTH} characters"
    if passphrase != passphrase.strip():
        return "no spaces at the start or the end"
    return None
