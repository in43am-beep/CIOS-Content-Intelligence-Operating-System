"""Auth flow tests: signup / login / me + token edge cases."""
from datetime import datetime, timedelta, timezone

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient

import ai_client
import app as app_module
from database import init_db

SECRET = "test-jwt-secret-123"


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setattr("config.settings.db_path", str(tmp_path / "test.db"))
    monkeypatch.setattr("config.settings.jwt_secret", SECRET)
    monkeypatch.setattr("config.settings.auth_required", True)
    monkeypatch.setattr("config.settings.min_seconds_between_calls", 0.0)
    app_module._throttle_hits.clear()
    init_db()
    monkeypatch.setattr(ai_client, "generate", lambda *a, **k: {"text": "x", "model": "m", "cached": False})
    return TestClient(app_module.app, raise_server_exceptions=False)


def test_signup_ok_first_user_admin(client):
    r = client.post("/api/v1/auth/signup", json={"email": "A@Test.com", "password": "password123"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["user"] == {"id": 1, "email": "a@test.com", "is_admin": True}
    payload = pyjwt.decode(body["data"]["token"], SECRET, algorithms=["HS256"])
    assert payload["sub"] == "1" and payload["is_admin"] is True


def test_signup_duplicate_400(client):
    client.post("/api/v1/auth/signup", json={"email": "a@test.com", "password": "password123"})
    r = client.post("/api/v1/auth/signup", json={"email": "a@test.com", "password": "password123"})
    assert r.status_code == 400
    assert r.json() == {"ok": False, "error": "Ye email pehle se registered hai — login karo."}


def test_signup_weak_password_400(client):
    r = client.post("/api/v1/auth/signup", json={"email": "a@test.com", "password": "short"})
    assert r.status_code == 400
    assert r.json()["ok"] is False and "8" in r.json()["error"]


def test_signup_bad_email_400(client):
    r = client.post("/api/v1/auth/signup", json={"email": "not-an-email", "password": "password123"})
    assert r.status_code == 400 and r.json()["ok"] is False


def test_second_user_not_admin(client):
    client.post("/api/v1/auth/signup", json={"email": "a@test.com", "password": "password123"})
    r = client.post("/api/v1/auth/signup", json={"email": "b@test.com", "password": "password123"})
    assert r.json()["data"]["user"]["is_admin"] is False


def test_login_ok(client):
    client.post("/api/v1/auth/signup", json={"email": "a@test.com", "password": "password123"})
    r = client.post("/api/v1/auth/login", json={"email": "a@test.com", "password": "password123"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["data"]["user"]["email"] == "a@test.com"


def test_login_wrong_password_401(client):
    client.post("/api/v1/auth/signup", json={"email": "a@test.com", "password": "password123"})
    r = client.post("/api/v1/auth/login", json={"email": "a@test.com", "password": "wrongpass1"})
    assert r.status_code == 401
    assert r.json()["ok"] is False


def test_login_unknown_email_401(client):
    r = client.post("/api/v1/auth/login", json={"email": "nobody@test.com", "password": "password123"})
    assert r.status_code == 401 and r.json()["ok"] is False


def test_me_ok(client):
    s = client.post("/api/v1/auth/signup", json={"email": "a@test.com", "password": "password123"}).json()
    r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {s['data']['token']}"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "data": {"user": s["data"]["user"]}}


def test_me_no_token_401(client):
    r = client.get("/api/v1/auth/me")
    assert r.status_code == 401
    assert r.json()["ok"] is False


def test_me_bad_token_401(client):
    r = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer garbage.token.here"})
    assert r.status_code == 401 and r.json()["ok"] is False


def test_me_wrong_scheme_401(client):
    r = client.get("/api/v1/auth/me", headers={"Authorization": "Token abc123"})
    assert r.status_code == 401 and r.json()["ok"] is False


def test_me_expired_token_401(client):
    token = pyjwt.encode(
        {"sub": "1", "email": "a@test.com", "is_admin": True,
         "exp": datetime.now(timezone.utc) - timedelta(seconds=1)},
        SECRET, algorithm="HS256")
    r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401 and r.json()["ok"] is False


def test_post_without_token_401(client):
    r = client.post("/api/v1/ideas", json={"niche": "dog stories"})
    assert r.status_code == 401 and r.json()["ok"] is False


def test_password_is_hashed_not_plaintext(client):
    client.post("/api/v1/auth/signup", json={"email": "a@test.com", "password": "password123"})
    from database import get_user_by_email
    user = get_user_by_email("a@test.com")
    assert user["pw_hash"] != "password123"
    assert user["pw_hash"].startswith("$argon2")
