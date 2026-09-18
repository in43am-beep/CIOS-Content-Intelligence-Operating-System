"""v2 contract tests (Builder A): /api/v1 prefix, X-Request-ID, envelopes, F-1.

- Purana /api/* prefix -> 404 (clean cut, V2-DESIGN.md §A5).
- X-Request-ID header har response par: 200, 401, 422, throttle-429, unhandled-500.
- F-1: generic-500 par security headers mojood hon (ServerErrorMiddleware fix).
- Envelope shapes: auth/history/usage/admin normalized; AI ApiResult + health + 422 exceptions.
"""
import logging
import re

import pytest
from fastapi.testclient import TestClient

import ai_client
import app as app_module
from database import init_db

FAKE = {"text": '{"ideas":[{"title":"Test","angle":"A","score":25,"why":"ok"}]}',
        "model": "test-model", "cached": False}

RID_RE = re.compile(r"^[0-9a-f]{16}$")
SEC_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
}


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setattr("config.settings.db_path", str(tmp_path / "test.db"))
    monkeypatch.setattr("config.settings.jwt_secret", "test-jwt-secret-123")
    monkeypatch.setattr("config.settings.auth_required", True)
    monkeypatch.setattr("config.settings.min_seconds_between_calls", 0.0)
    app_module._throttle_hits.clear()
    init_db()
    monkeypatch.setattr(ai_client, "generate", lambda *a, **k: dict(FAKE))
    return TestClient(app_module.app, raise_server_exceptions=False)


def signup(client, email="v1@test.com", password="password123"):
    r = client.post("/api/v1/auth/signup", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["data"]


def auth_headers(client, email="v1@test.com"):
    data = signup(client, email=email)
    return {"Authorization": f"Bearer {data['token']}"}, data


def assert_request_id(r):
    rid = r.headers.get("X-Request-ID")
    assert rid and RID_RE.match(rid), f"bad X-Request-ID: {rid!r}"
    return rid


def assert_sec_headers(r):
    for k, v in SEC_HEADERS.items():
        assert r.headers.get(k) == v, f"{k} missing/wrong on {r.status_code}"


# ------------------------------------------------------- clean cut: old 404 ---
def test_old_prefix_404s(client):
    headers, _ = auth_headers(client)
    cases = [
        ("get", "/api/health", {}),
        ("get", "/api/auth/me", {}),
        ("get", "/api/history", {}),
        ("get", "/api/usage", {}),
        ("get", "/api/admin/users", {}),
        ("post", "/api/ideas", {"json": {"niche": "dog stories"}}),
        ("post", "/api/auth/login", {"json": {"email": "x@y.z", "password": "password123"}}),
    ]
    for method, path, kwargs in cases:
        kw = dict(kwargs); kw["headers"] = headers
        r = getattr(client, method)(path, **kw)
        assert r.status_code == 404, path


# ------------------------------------------------------- X-Request-ID ---
def test_request_id_on_200(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert_request_id(r)
    assert_sec_headers(r)


def test_request_id_on_401(client):
    r = client.get("/api/v1/history")  # bina token
    assert r.status_code == 401
    assert_request_id(r)
    assert_sec_headers(r)


def test_request_id_on_422(client, monkeypatch):
    headers, _ = auth_headers(client)
    r = client.post("/api/v1/ideas", headers=headers, json={"niche": "x"})  # too short
    assert r.status_code == 422
    assert_request_id(r)
    assert_sec_headers(r)


def test_request_id_on_throttle_429(client):
    headers, _ = auth_headers(client)
    last = None
    for _ in range(31):
        last = client.post("/api/v1/ideas", headers=headers, json={"niche": "dog stories"})
    assert last.status_code == 429
    assert_request_id(last)
    assert_sec_headers(last)


def test_request_id_and_sec_headers_on_unhandled_500(client, monkeypatch):
    # F-1: ServerErrorMiddleware ka response middleware stack se nahi guzarta —
    # unhandled_handler headers explicitly lagata hai.
    headers, _ = auth_headers(client)

    def boom(*a, **k):
        raise RuntimeError("RAW-MARKER-V1 provider exploded")
    monkeypatch.setattr(ai_client, "generate", boom)
    r = client.post("/api/v1/ideas", headers=headers, json={"niche": "dog stories"})
    assert r.status_code == 500
    assert r.json() == {"ok": False, "error": "Server error"}
    assert "RAW-MARKER-V1" not in r.text  # koi raw leakage nahi
    rid = assert_request_id(r)
    assert_sec_headers(r)
    return rid


def test_request_id_in_server_logs(client, caplog):
    with caplog.at_level(logging.INFO, logger="cios.app"):
        r = client.get("/api/v1/health")
    rid = r.headers["X-Request-ID"]
    assert rid in caplog.text  # rid log line me
    assert re.search(r"rid=[0-9a-f]{16}", caplog.text)


def test_idempotency_key_logged(client, caplog):
    headers, _ = auth_headers(client)
    headers["X-Idempotency-Key"] = "test-idem-key-abc123"
    with caplog.at_level(logging.INFO, logger="cios.app"):
        r = client.post("/api/v1/ideas", headers=headers, json={"niche": "dog stories"})
    assert r.status_code == 200
    assert "test-idem-key-abc123" in caplog.text  # server log me idempotency key


# ------------------------------------------------------- envelope shapes ---
def test_envelope_signup_login_me(client):
    r = client.post("/api/v1/auth/signup",
                    json={"email": "env@test.com", "password": "password123"})
    body = r.json()
    assert body["ok"] is True
    assert set(body["data"].keys()) == {"token", "user"}
    assert body["data"]["user"]["email"] == "env@test.com"

    r = client.post("/api/v1/auth/login",
                    json={"email": "env@test.com", "password": "password123"})
    assert set(r.json()["data"].keys()) == {"token", "user"}

    h = {"Authorization": f"Bearer {r.json()['data']['token']}"}
    r = client.get("/api/v1/auth/me", headers=h)
    assert r.json() == {"ok": True, "data": {"user": r.json()["data"]["user"]}}
    assert set(r.json()["data"].keys()) == {"user"}


def test_envelope_history_usage(client):
    headers, data = auth_headers(client)
    client.post("/api/v1/ideas", headers=headers, json={"niche": "dog stories"})
    r = client.get("/api/v1/history?limit=5", headers=headers)
    body = r.json()
    assert body["ok"] is True
    assert set(body["data"].keys()) == {"items", "limit"}
    assert body["data"]["limit"] == 5
    assert body["data"]["items"][0]["feature"] == "ideas"

    r = client.get("/api/v1/usage", headers=headers)
    assert set(r.json()["data"].keys()) == {"used", "cap", "day"}
    # NOTE: ai_client.generate is mocked in tests, so real attempt-based
    # accounting is bypassed here — used==0. Shape (keys) is what this asserts.
    assert r.json()["data"]["used"] == 0
    assert r.json()["data"]["cap"] == 48


def test_envelope_admin(client):
    headers, admin_data = auth_headers(client)
    u2 = signup(client, email="env2@test.com")
    r = client.get("/api/v1/admin/users", headers=headers)
    assert set(r.json()["data"].keys()) == {"users"}
    assert all("pw_hash" not in u for u in r.json()["data"]["users"])

    r = client.get("/api/v1/admin/usage", headers=headers)
    assert set(r.json()["data"].keys()) == {"day", "rows"}

    r = client.post("/api/v1/admin/quota", headers=headers,
                    json={"user_id": u2["user"]["id"], "cap": 7})
    assert r.json() == {"ok": True, "data": {"user_id": u2["user"]["id"], "cap": 7}}


def test_ai_result_shape_kept(client):
    # §A5 exception: 6 AI POSTs {ok, model, cached, data} rehte hain.
    headers, _ = auth_headers(client)
    r = client.post("/api/v1/ideas", headers=headers, json={"niche": "dog stories"})
    body = r.json()
    assert body["ok"] is True
    assert body["model"] == "test-model" and body["cached"] is False
    assert body["data"]["ideas"][0]["title"] == "Test"


def test_health_shape_kept(client):
    # §A5 exception: health flat rehta hai (monitors sirf `ok` parhte hain).
    r = client.get("/api/v1/health")
    body = r.json()
    assert body["ok"] is True
    assert "version" in body and "key_configured" in body
    assert "data" not in body


def test_422_shape_kept(client):
    # §A5 exception: 422 FastAPI {detail:[...]} rehta hai.
    headers, _ = auth_headers(client)
    r = client.post("/api/v1/ideas", headers=headers, json={"niche": "x"})
    assert r.status_code == 422
    assert isinstance(r.json()["detail"], list)
