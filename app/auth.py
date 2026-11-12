"""Verifies the Supabase access token the website sends as `Authorization: Bearer <token>`."""
import threading
import time

import httpx
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import settings

_bearer = HTTPBearer(auto_error=False)
_cache: dict[str, tuple[str, float]] = {}
_lock = threading.Lock()
_jwk_client: jwt.PyJWKClient | None = None


class AuthError(Exception):
    pass


def _jwks() -> jwt.PyJWKClient:
    global _jwk_client
    if _jwk_client is None:
        _jwk_client = jwt.PyJWKClient(f"{settings.supabase_url}/auth/v1/.well-known/jwks.json")
    return _jwk_client


def _verify_locally(token: str) -> tuple[str, float] | None:
    try:
        alg = jwt.get_unverified_header(token).get("alg")
        if alg == "HS256" and settings.supabase_jwt_secret:
            key = settings.supabase_jwt_secret
        elif alg in ("ES256", "RS256") and settings.supabase_url:
            key = _jwks().get_signing_key_from_jwt(token).key
        else:
            return None
        claims = jwt.decode(token, key, algorithms=[alg], audience="authenticated")
        return claims["sub"], float(claims.get("exp", time.time() + 60))
    except jwt.ExpiredSignatureError:
        raise AuthError("Your session expired. Please sign in again.")
    except Exception:
        return None  # fall back to asking Supabase


def _verify_remotely(token: str) -> tuple[str, float]:
    if not settings.supabase_url or not settings.supabase_anon_key:
        raise AuthError("Backend is missing SUPABASE_URL / SUPABASE_ANON_KEY.")
    try:
        r = httpx.get(
            f"{settings.supabase_url}/auth/v1/user",
            headers={"apikey": settings.supabase_anon_key, "Authorization": f"Bearer {token}"},
            timeout=8,
        )
    except httpx.HTTPError:
        raise AuthError("Could not reach the sign-in service. Try again.")
    if r.status_code != 200:
        raise AuthError("Your session is not valid. Please sign in again.")
    return r.json()["id"], time.time() + 60


def verify_token(token: str | None) -> str:
    """Returns the Supabase user id, or raises AuthError."""
    if not token:
        raise AuthError("Sign in to continue.")
    now = time.time()
    with _lock:
        hit = _cache.get(token)
    if hit and hit[1] > now:
        return hit[0]
    result = _verify_locally(token) or _verify_remotely(token)
    with _lock:
        if len(_cache) > 5000:
            _cache.clear()
        _cache[token] = (result[0], min(result[1], now + 300))
    return result[0]


def current_user(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> str:
    try:
        return verify_token(creds.credentials if creds else None)
    except AuthError as e:
        raise HTTPException(status_code=401, detail=str(e))
