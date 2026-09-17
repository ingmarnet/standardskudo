"""Autenticación JWT para la API.

Tokens HMAC-SHA256, sin librería externa: el payload es tan simple (id, email,
rol, exp) que no justifica una dependencia. El secreto se lee de la
configuración; si no existe, se genera uno efímero y se advierte en stderr.
"""

import hashlib
import hmac
import json
import os
import sys
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

TOKEN_DURATION_HOURS = 12

_bearer = HTTPBearer()

_secret: bytes | None = None


def _get_secret() -> bytes:
    global _secret
    if _secret is not None:
        return _secret
    raw = os.environ.get("SKUDO_JWT_SECRET", "")
    if not raw:
        import secrets

        raw = secrets.token_hex(32)
        print(
            "ADVERTENCIA: SKUDO_JWT_SECRET no definido, se generó uno efímero. "
            "Los tokens no sobrevivirán un reinicio.",
            file=sys.stderr,
        )
    _secret = raw.encode()
    return _secret


def _b64(data: bytes) -> str:
    return urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    pad = 4 - len(s) % 4
    return urlsafe_b64decode(s + "=" * pad)


def create_token(user_id: int, email: str, role: str) -> str:
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64(
        json.dumps(
            {
                "sub": user_id,
                "email": email,
                "role": role,
                "exp": int(time.time()) + TOKEN_DURATION_HOURS * 3600,
            }
        ).encode()
    )
    sig = _b64(
        hmac.new(_get_secret(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    )
    return f"{header}.{payload}.{sig}"


def verify_token(token: str) -> dict:
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("token mal formado")
    header, payload, sig = parts
    expected = _b64(
        hmac.new(
            _get_secret(), f"{header}.{payload}".encode(), hashlib.sha256
        ).digest()
    )
    if not hmac.compare_digest(sig, expected):
        raise ValueError("firma inválida")
    data = json.loads(_unb64(payload))
    if data.get("exp", 0) < time.time():
        raise ValueError("token expirado")
    return data


def require_user(creds: HTTPAuthorizationCredentials = Depends(_bearer)) -> dict:
    try:
        return verify_token(creds.credentials)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
        )
