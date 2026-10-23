"""Origin-side Cloudflare Access verification, independent of the tunnel."""
from __future__ import annotations

from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit

import jwt
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from tinystudio.config import config


class AccessConfigurationError(ValueError):
    """An incomplete Access deployment must never silently become public."""


class AccessVerifier:
    """Own the configured issuer, audience, and rotating public-key cache."""

    def __init__(self, team_domain: str, audience: str) -> None:
        issuer = team_domain.rstrip("/")
        if "://" not in issuer:
            issuer = "https://" + issuer
        parts = urlsplit(issuer)
        if (parts.scheme != "https" or not parts.hostname or not parts.hostname.endswith(".cloudflareaccess.com")
                or parts.username or parts.password or parts.port or parts.path or parts.query or parts.fragment):
            raise AccessConfigurationError("Access team domain must be an HTTPS cloudflareaccess.com origin")
        if not audience:
            raise AccessConfigurationError("Access audience is required")
        self._issuer = issuer
        self._audience = audience
        self._keys = jwt.PyJWKClient(f"{issuer}/cdn-cgi/access/certs", timeout=5, lifespan=300)

    def verify(self, token: str) -> dict[str, Any]:
        if jwt.get_unverified_header(token).get("alg") != "RS256":
            raise jwt.InvalidAlgorithmError("Access requires RS256")
        key = self._keys.get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=["RS256"], audience=self._audience, issuer=self._issuer,
                            options={"require": ["exp", "iat", "iss", "aud", "type"]})
        if claims["type"] != "app":
            raise jwt.InvalidTokenError("an application Access token is required")
        return claims


@lru_cache(maxsize=4)
def _verifier(team_domain: str, audience: str) -> AccessVerifier:
    return AccessVerifier(team_domain, audience)


class CloudflareAccessMiddleware:
    """Check every HTTP route; deployment liveness exposes only a constant result."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or (scope["path"] == "/healthz" and scope["method"] in ("GET", "HEAD")):
            await self.app(scope, receive, send)
            return
        cfg = config()
        team, audience = cfg.cloudflare_access_team_domain, cfg.cloudflare_access_audience
        if not team and not audience and not cfg.cloudflare_access_required:
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        try:
            if not team or not audience:
                raise AccessConfigurationError("Access team domain and audience must both be configured")
            verifier = _verifier(team, audience)
        except ValueError:
            await JSONResponse({"detail": "Cloudflare Access is not configured correctly"}, status_code=503)(scope, receive, send)
            return
        token = request.headers.get("cf-access-jwt-assertion")
        if not token:
            if cfg.cloudflare_access_required:
                await JSONResponse({"detail": "Cloudflare Access assertion required"}, status_code=401)(scope, receive, send)
                return
        else:
            try:
                claims = await run_in_threadpool(verifier.verify, token)
            except (jwt.PyJWTError, ValueError, TypeError, KeyError):
                await JSONResponse({"detail": "Invalid Cloudflare Access assertion"}, status_code=401)(scope, receive, send)
                return
            scope.setdefault("state", {})["cloudflare_claims"] = claims
            scope["state"]["cloudflare_assertion"] = token
        await self.app(scope, receive, send)
