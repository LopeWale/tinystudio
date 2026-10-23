"""Offline origin authentication and client credential-boundary checks."""
from __future__ import annotations

import json
import time
from dataclasses import replace

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from tinystudio.api import deps
from tinystudio.api.routes import auth
from tinystudio.auth import issue_service_token, issue_session
from tinystudio.auth.cloudflare import AccessVerifier, CloudflareAccessMiddleware, _verifier
from tinystudio.config import config
from tinystudio.db.models import ApiKey, Instance, User
from tinystudio.integrations.tools import Studio, StudioError

ISSUER = "https://test-team.cloudflareaccess.com"
PUBLIC = "https://tinystudio.example.com"


@pytest.fixture
def signing(monkeypatch):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    jwk.update(kid="test-key", alg="RS256", use="sig")
    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", lambda self: {"keys": [jwk]})
    _verifier.cache_clear()
    def token(**overrides):
        claims = {"iss": ISSUER, "aud": ["test-aud"], "exp": int(time.time()) + 600,
                  "iat": int(time.time()), "type": "app", "email": "member@example.com", "sub": "human"}
        claims.update(overrides)
        return jwt.encode(claims, private, algorithm="RS256", headers={"kid": "test-key"})
    return token


@pytest.fixture
def origin(monkeypatch, signing):
    cfg = replace(config(), cloudflare_access_team_domain=ISSUER, cloudflare_access_audience="test-aud", cloudflare_access_required=True)
    monkeypatch.setattr("tinystudio.auth.cloudflare.config", lambda: cfg)
    monkeypatch.setattr(auth, "config", lambda: cfg)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    for table in (Instance.__table__, User.__table__, ApiKey.__table__):
        table.create(engine)
    db = Session(engine)
    db.add(Instance(id=1, secret_key="local-secret", internal_token="internal"))
    db.add_all([User(id="member", email="member@example.com", name="Member", password_hash="unused", role="member", disabled=False),
                User(id="admin", email="admin@example.com", name="Admin", password_hash="unused", role="admin", disabled=False),
                User(id="disabled", email="disabled@example.com", name="Disabled", password_hash="unused", role="admin", disabled=True)])
    db.commit()
    deps.forget_secret()
    app = FastAPI()
    app.add_middleware(CloudflareAccessMiddleware)
    app.dependency_overrides[deps.get_db] = lambda: db
    app.include_router(auth.router)
    @app.get("/admin")
    def admin(principal=Depends(deps.require_admin)):
        return principal.as_dict()
    @app.api_route("/healthz", methods=["GET", "HEAD"])
    def health():
        return {"ok": True}
    with TestClient(app) as client:
        yield client, cfg, db
    db.close()
    engine.dispose()
    deps.forget_secret()


def test_real_signature_and_required_claims(signing):
    verifier = AccessVerifier(ISSUER, "test-aud")
    assert verifier.verify(signing())["email"] == "member@example.com"
    for overrides in ({"aud": ["another-app"]}, {"iss": "https://other.cloudflareaccess.com"},
                      {"exp": int(time.time()) - 10}, {"iat": int(time.time()) + 100}, {"type": "org"}):
        with pytest.raises(jwt.PyJWTError):
            verifier.verify(signing(**overrides))
    forged = jwt.encode({"aud": ["test-aud"]}, "a" * 32, algorithm="HS256", headers={"kid": "test-key"})
    with pytest.raises(jwt.InvalidAlgorithmError):
        verifier.verify(forged)
    parts = signing().split(".")
    parts[1] = jwt.utils.base64url_encode(json.dumps({"email": "admin@example.com"}).encode()).decode()
    with pytest.raises(jwt.InvalidSignatureError):
        verifier.verify(".".join(parts))


def test_origin_denies_missing_spoofed_and_invalid_assertions(origin):
    client, _, _ = origin
    for headers in ({}, {"Host": "localhost", "CF-Access-Authenticated-User-Email": "admin@example.com", "X-Forwarded-For": "127.0.0.1"},
                    {"Cf-Access-Jwt-Assertion": "invalid"}):
        assert client.get("/api/auth/me", headers=headers).status_code == 401
    assert client.get("/healthz").json() == {"ok": True}
    assert client.head("/healthz").status_code == 200
    assert client.post("/healthz").status_code == 401


def test_human_mapping_roles_csrf_logout_and_no_bootstrap(origin, signing):
    client, _, db = origin
    headers = {"Cf-Access-Jwt-Assertion": signing()}
    assert client.get("/api/auth/me", headers=headers).json()["via"] == "cloudflare"
    assert client.get("/admin", headers=headers).status_code == 403
    assert client.get("/admin", headers={"Cf-Access-Jwt-Assertion": signing(email="admin@example.com")}).status_code == 200
    for email in ("unknown@example.com", "disabled@example.com"):
        assert client.get("/api/auth/me", headers={"Cf-Access-Jwt-Assertion": signing(email=email)}).status_code == 403
    assert client.post("/api/auth/logout", headers=headers).status_code == 403
    headers["X-Requested-With"] = "tinystudio"
    assert client.post("/api/auth/logout", headers=headers).json()["logout_url"] == "/cdn-cgi/access/logout"
    assert client.post("/api/auth/setup", headers=headers, json={"email": "new@example.com", "password": "long-enough"}).status_code == 403
    assert db.query(User).count() == 3


def test_machine_needs_application_bearer_and_preserves_role(origin, signing):
    client, _, _ = origin
    headers = {"Cf-Access-Jwt-Assertion": signing(email=None, sub="", common_name="service.access")}
    client.cookies.set("tinystudio_session", issue_session("local-secret", "admin"))
    assert client.get("/api/auth/me", headers=headers).status_code == 401
    headers["Authorization"] = "Bearer " + issue_service_token("local-secret", "member")
    assert client.get("/api/auth/me", headers=headers).json()["role"] == "member"
    assert client.get("/admin", headers=headers).status_code == 403
    headers["Cf-Access-Jwt-Assertion"] = signing(email="admin@example.com")
    assert client.get("/api/auth/me", headers=headers).status_code == 403


def test_disabled_local_and_partial_settings(origin, monkeypatch):
    client, cfg, _ = origin
    monkeypatch.setattr("tinystudio.auth.cloudflare.config", lambda: replace(cfg, cloudflare_access_required=False, cloudflare_access_team_domain=None, cloudflare_access_audience=None))
    client.cookies.set("tinystudio_session", issue_session("local-secret", "member"))
    assert client.get("/api/auth/me").json()["via"] == "session"
    monkeypatch.setattr("tinystudio.auth.cloudflare.config", lambda: replace(cfg, cloudflare_access_audience=None))
    assert client.get("/api/auth/me").status_code == 503


@pytest.fixture
def client_env(monkeypatch):
    monkeypatch.setenv("CF_ACCESS_CLIENT_ID", "client-id")
    monkeypatch.setenv("CF_ACCESS_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("TINYSTUDIO_PUBLIC_URL", PUBLIC)


def test_sdk_credentials_bound_to_public_origin_and_relative_calls(client_env):
    studio = Studio(PUBLIC, token="ts-app")
    assert studio.http.headers["CF-Access-Client-Id"] == "client-id"
    assert studio.http.headers["authorization"] == "Bearer ts-app"
    for url in ("http://tinystudio.example.com", "https://evil.example.com", "http://127.0.0.1:8000"):
        local = Studio(url)
        assert "CF-Access-Client-Secret" not in local.http.headers
        local.http.close()
    for path in ("https://evil.example.com/api", "//evil.example.com/api", "/\\evil.example.com"):
        with pytest.raises(ValueError):
            studio.call("GET", path)
    with pytest.raises(ValueError):
        Studio("https://evil.example.com", cf_access_client_id="id", cf_access_client_secret="secret")
    studio.http.close()


def test_sdk_partial_credentials_and_forwarded_assertion_boundary(client_env, monkeypatch):
    monkeypatch.delenv("CF_ACCESS_CLIENT_SECRET")
    with pytest.raises(ValueError, match="both"):
        Studio(PUBLIC)
    studio = Studio("http://127.0.0.1:8000", cf_access_client_id="", cf_access_client_secret="", access_assertion="validated")
    assert studio.http.headers["Cf-Access-Jwt-Assertion"] == "validated"
    with pytest.raises(ValueError, match="loopback"):
        Studio(PUBLIC, cf_access_client_id="", cf_access_client_secret="", access_assertion="validated")
    studio.http.close()


@pytest.mark.parametrize("status,body,headers", [(302, "", {"location": "https://login.example.com"}), (200, "<html>Login</html>", {"content-type": "text/html"})])
def test_sdk_rejects_access_redirect_or_html(client_env, status, body, headers):
    studio = Studio(PUBLIC)
    studio.http.close()
    studio.http = httpx.Client(base_url=PUBLIC, transport=httpx.MockTransport(lambda request: httpx.Response(status, text=body, headers=headers)))
    with pytest.raises(StudioError):
        studio.call("GET", "/api/health")
    studio.http.close()


def test_dataset_upload_transfers_client_file_to_r2_without_app_secrets(client_env, monkeypatch, tmp_path):
    source = tmp_path / "rows.jsonl"
    source.write_bytes(b'{"messages":[]}\n')
    studio = Studio(PUBLIC, "home", "ts-app")
    application_headers = dict(studio.http.headers)
    studio.http.close()
    requests = []
    def api(request):
        requests.append(request)
        assert request.headers["CF-Access-Client-Secret"] == "client-secret"
        if request.url.path == "/api/p/home/artifacts/config":
            return httpx.Response(200, json={"direct_upload": True})
        if request.url.path == "/api/p/home/artifacts/uploads":
            assert json.loads(request.content)["size"] == len(source.read_bytes())
            return httpx.Response(200, json={"url": "https://account.r2.cloudflarestorage.com/bucket/key?signature=redacted",
                                             "key": "project/uploads/key", "headers": {"Content-Type": "application/x-ndjson"}})
        assert request.url.path == "/api/p/home/datasets/from-object"
        assert json.loads(request.content) == {"name": "training", "key": "project/uploads/key"}
        return httpx.Response(200, json={"name": "training"})
    studio.http = httpx.Client(base_url=PUBLIC, headers=application_headers, transport=httpx.MockTransport(api))
    original_client = httpx.Client
    uploads = []
    def upload(request):
        uploads.append(request)
        assert request.method == "PUT"
        assert request.content == source.read_bytes()
        assert request.headers["Content-Length"] == str(len(source.read_bytes()))
        assert "authorization" not in request.headers
        assert "cf-access-client-secret" not in request.headers
        assert "cf-access-client-id" not in request.headers
        return httpx.Response(200)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original_client(transport=httpx.MockTransport(upload), **kwargs))
    assert studio.upload_dataset("training", str(source)) == {"name": "training"}
    assert len(requests) == 3 and len(uploads) == 1
    studio.http.close()


def test_dataset_upload_local_multipart_and_bad_destination(client_env, monkeypatch, tmp_path):
    source = tmp_path / "rows.jsonl"
    source.write_bytes(b'{"messages":[]}\n')
    studio = Studio("http://127.0.0.1:8000", "home")
    studio.http.close()
    def api(request):
        if request.url.path.endswith("/artifacts/config"):
            return httpx.Response(200, json={"direct_upload": False})
        assert request.url.path == "/api/p/home/datasets/upload"
        assert b'name="file"; filename="rows.jsonl"' in request.content
        assert source.read_bytes() in request.content
        assert b'name="name"' in request.content and b"training" in request.content
        return httpx.Response(200, json={"name": "training"})
    studio.http = httpx.Client(base_url="http://127.0.0.1:8000", transport=httpx.MockTransport(api))
    assert studio.upload_dataset("training", str(source)) == {"name": "training"}
    monkeypatch.setattr(studio, "call", lambda method, path, **kwargs: {"direct_upload": True} if method == "GET" else {"url": "https://evil.example.com/upload", "key": "key"})
    with pytest.raises(StudioError, match="destination"):
        studio.upload_dataset("training", str(source))
    studio.http.close()


def test_empty_jwks_and_missing_expiration_fail_closed(signing, monkeypatch):
    verifier = AccessVerifier("test-team.cloudflareaccess.com", "test-aud")
    assert verifier.verify(signing())["type"] == "app"
    with pytest.raises(jwt.MissingRequiredClaimError):
        verifier.verify(signing(exp=None))
    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", lambda self: {"keys": []})
    uncached = AccessVerifier(ISSUER, "test-aud")
    with pytest.raises(jwt.PyJWTError):
        uncached.verify(signing())


def test_jwks_outage_and_optional_invalid_token_fail_closed(origin, monkeypatch, signing):
    client, cfg, _ = origin
    def unavailable(self):
        raise jwt.PyJWKClientConnectionError("unavailable")
    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", unavailable)
    assert client.get("/api/auth/me", headers={"Cf-Access-Jwt-Assertion": signing()}).status_code == 401
    monkeypatch.setattr("tinystudio.auth.cloudflare.config", lambda: replace(cfg, cloudflare_access_required=False))
    assert client.get("/api/auth/me", headers={"Cf-Access-Jwt-Assertion": "invalid"}).status_code == 401


def test_machine_api_key_and_disabled_account(origin, signing):
    from tinystudio.auth import key_digest
    client, _, db = origin
    db.add(ApiKey(id="key", user_id="member", name="MCP", prefix="ts-test", sha256=key_digest("ts-test-key")))
    db.commit()
    headers = {"Cf-Access-Jwt-Assertion": signing(email=None, sub="", common_name="service.access"), "Authorization": "Bearer ts-test-key"}
    assert client.get("/api/auth/me", headers=headers).json()["via"] == "api_key"
    assert client.get("/admin", headers=headers).status_code == 403
    db.get(User, "member").disabled = True
    db.commit()
    assert client.get("/api/auth/me", headers=headers).status_code == 401
