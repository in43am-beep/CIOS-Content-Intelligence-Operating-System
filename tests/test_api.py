"""CIOS API tests — AI calls are mocked, so tests cost $0 and need no API key.

AUTH_REQUIRED=true in this suite (the publish configuration). Every test gets a
fresh temp DB + JWT secret; helper signs up a user and returns a Bearer token.
"""
import pytest
from fastapi.testclient import TestClient

import ai_client
import app as app_module
import routers
from database import init_db

FAKE = {"text": '{"ideas":[{"title":"Test","angle":"A","score":25,"why":"ok"}]}',
        "model": "test-model", "cached": False}


@pytest.fixture()
def client(monkeypatch, tmp_path):
    # Har test apne temp DB pe chale — asli cios.db clean rahe.
    monkeypatch.setattr("config.settings.db_path", str(tmp_path / "test.db"))
    monkeypatch.setattr("config.settings.jwt_secret", "test-jwt-secret-123")
    monkeypatch.setattr("config.settings.auth_required", True)
    monkeypatch.setattr("config.settings.min_seconds_between_calls", 0.0)
    app_module._throttle_hits.clear()  # per-IP throttle state teston me leak na ho
    init_db()
    monkeypatch.setattr(ai_client, "generate", lambda *a, **k: dict(FAKE))
    return TestClient(app_module.app, raise_server_exceptions=False)


def signup(client, email="a@test.com", password="password123"):
    r = client.post("/api/v1/auth/signup", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["data"]["token"]
    return body


@pytest.fixture()
def auth_headers(client):
    data = signup(client)
    return {"Authorization": f"Bearer {data['data']['token']}"}, data


# ------------------------------------------------------------- health ---
def test_health_minimal_unauthenticated(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["version"] == "1.0.0"
    assert "key_configured" in body
    assert "models" not in body  # detailed info sirf authed users ko


def test_health_detailed_authed(client, auth_headers):
    headers, _ = auth_headers
    r = client.get("/api/v1/health", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["models"], list) and body["models"]


# ------------------------------------------------------- 6 AI endpoints ---
def test_ideas(client, auth_headers):
    headers, _ = auth_headers
    r = client.post("/api/v1/ideas", headers=headers,
                    json={"niche": "dog stories", "audience": "seniors", "count": 5})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["model"] == "test-model"
    assert body["data"]["ideas"][0]["title"] == "Test"


def test_research(client, auth_headers):
    headers, _ = auth_headers
    r = client.post("/api/v1/research", headers=headers, json={"niche": "dog stories"})
    assert r.status_code == 200 and r.json()["ok"] is True


def test_scripts(client, auth_headers):
    headers, _ = auth_headers
    r = client.post("/api/v1/scripts", headers=headers,
                    json={"topic": "a lost puppy", "duration_sec": 60})
    assert r.status_code == 200 and r.json()["ok"] is True


def test_packaging(client, auth_headers):
    headers, _ = auth_headers
    r = client.post("/api/v1/packaging", headers=headers, json={"topic": "a lost puppy"})
    assert r.status_code == 200 and r.json()["ok"] is True


def test_seo(client, auth_headers):
    headers, _ = auth_headers
    r = client.post("/api/v1/seo", headers=headers,
                    json={"title": "Lost Puppy Found", "topic": "puppy rescue"})
    assert r.status_code == 200 and r.json()["ok"] is True


def test_niche(client, auth_headers):
    headers, _ = auth_headers
    r = client.post("/api/v1/niche", headers=headers, json={"niche": "AI history documentaries"})
    assert r.status_code == 200 and r.json()["ok"] is True


def test_ideas_validation(client, auth_headers):
    headers, _ = auth_headers
    r = client.post("/api/v1/ideas", headers=headers, json={"niche": "x"})  # too short
    assert r.status_code == 422


def test_whitespace_rejected(client, auth_headers):
    headers, _ = auth_headers
    r = client.post("/api/v1/ideas", headers=headers, json={"niche": "   "})
    assert r.status_code == 422
    r = client.post("/api/v1/scripts", headers=headers,
                    json={"topic": " \t ", "duration_sec": 60})
    assert r.status_code == 422


def test_user_prompt_wrapped_in_delimiters(client, auth_headers, monkeypatch):
    headers, _ = auth_headers
    seen = {}

    def rec(system_prompt, user_prompt, max_tokens=2000, user_id=0):
        seen["user"] = user_prompt
        seen["system"] = system_prompt
        seen["user_id"] = user_id
        return dict(FAKE)

    monkeypatch.setattr(ai_client, "generate", rec)
    r = client.post("/api/v1/ideas", headers=headers, json={"niche": "dog stories"})
    assert r.status_code == 200
    assert "<<<USER_INPUT>>>" in seen["user"] and "<<<END>>>" in seen["user"]
    assert "INSTRUCTION HIERARCHY" in seen["system"]  # brain guard line
    assert seen["user_id"] == auth_headers[1]["data"]["user"]["id"]


# ------------------------------------------------------------- history ---
def test_history_records(client, auth_headers):
    headers, data = auth_headers
    client.post("/api/v1/ideas", headers=headers, json={"niche": "dog stories"})
    r = client.get("/api/v1/history?limit=5", headers=headers)
    assert r.status_code == 200
    items = r.json()["data"]["items"]
    assert len(items) >= 1 and items[0]["feature"] == "ideas"
    assert items[0]["user_id"] == data["data"]["user"]["id"]


def test_history_isolation(client):
    u1 = signup(client, email="u1@test.com")
    u2 = signup(client, email="u2@test.com")
    h1 = {"Authorization": f"Bearer {u1['data']['token']}"}
    h2 = {"Authorization": f"Bearer {u2['data']['token']}"}
    client.post("/api/v1/ideas", headers=h1, json={"niche": "dog stories"})
    r2 = client.get("/api/v1/history?limit=5", headers=h2)
    assert r2.status_code == 200 and r2.json()["data"]["items"] == []  # B user ko A ki rows nahi
    r1 = client.get("/api/v1/history?limit=5", headers=h1)
    assert len(r1.json()["data"]["items"]) == 1


def test_history_no_token_401(client):
    r = client.get("/api/v1/history")
    assert r.status_code == 401 and r.json()["ok"] is False


# ------------------------------------------------------- error mapping ---
def test_quota_exceeded_maps_to_429(client, auth_headers, monkeypatch):
    headers, _ = auth_headers

    def boom(*a, **k):
        raise ai_client.QuotaExceeded("limit poori")
    monkeypatch.setattr(ai_client, "generate", boom)
    r = client.post("/api/v1/ideas", headers=headers, json={"niche": "dog stories"})
    assert r.status_code == 429
    assert "limit" in r.json()["error"]


def test_missing_key_maps_to_400(client, auth_headers, monkeypatch):
    headers, _ = auth_headers

    def boom(*a, **k):
        raise ai_client.MissingApiKey("key set nahi")
    monkeypatch.setattr(ai_client, "generate", boom)
    r = client.post("/api/v1/ideas", headers=headers, json={"niche": "dog stories"})
    assert r.status_code == 400
    assert "key" in r.json()["error"]


def test_generic_500_shape_never_echoes_raw(client, auth_headers, monkeypatch):
    headers, _ = auth_headers

    def boom(*a, **k):
        raise RuntimeError("RAW-SECRET-MARKER provider exploded")
    monkeypatch.setattr(ai_client, "generate", boom)
    r = client.post("/api/v1/ideas", headers=headers, json={"niche": "dog stories"})
    assert r.status_code == 500
    body = r.json()
    assert body == {"ok": False, "error": "Server error"}
    assert "RAW-SECRET-MARKER" not in r.text


def test_raw_text_fallback(client, auth_headers, monkeypatch):
    headers, _ = auth_headers
    monkeypatch.setattr(ai_client, "generate",
                        lambda *a, **k: {"text": "yeh plain jawab hai", "model": "m", "cached": False})
    r = client.post("/api/v1/ideas", headers=headers, json={"niche": "dog stories"})
    assert r.status_code == 200
    assert r.json()["data"]["text"] == "yeh plain jawab hai"


def test_save_history_failure_does_not_500(client, auth_headers, monkeypatch):
    headers, _ = auth_headers

    def boom(*a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(routers, "save_history", boom)
    r = client.post("/api/v1/ideas", headers=headers, json={"niche": "dog stories"})
    assert r.status_code == 200 and r.json()["ok"] is True  # best-effort (B-5)


# ---------------------------------------------------------------- usage ---
def test_usage_endpoint(client, auth_headers):
    headers, data = auth_headers
    r = client.get("/api/v1/usage", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["used"] == 0 and body["data"]["cap"] == 48


# ------------------------------------------------------- security headers ---
def test_security_headers_present(client):
    r = client.get("/api/v1/health")
    assert r.headers["Content-Security-Policy"] == \
        "default-src 'self'; script-src 'self'; style-src 'self'"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Referrer-Policy"] == "same-origin"


# ------------------------------------------------------------- throttle ---
def test_per_ip_throttle_429(client, auth_headers):
    headers, _ = auth_headers
    last = None
    for _ in range(31):
        last = client.post("/api/v1/ideas", headers=headers, json={"niche": "dog stories"})
    assert last.status_code == 429
    assert last.json() == {"ok": False, "error": "Bohat zyada requests — ek minute ruk kar try karo."}
    assert "Content-Security-Policy" in last.headers  # headers bhi 429 pe


# ---------------------------------------------------------------- admin ---
def test_admin_users(client, auth_headers):
    headers, data = auth_headers
    assert data["data"]["user"]["is_admin"] is True  # pehla user = admin
    r = client.get("/api/v1/admin/users", headers=headers)
    assert r.status_code == 200
    users = r.json()["data"]["users"]
    assert any(u["email"] == "a@test.com" and u["is_admin"] for u in users)
    assert all("pw_hash" not in u for u in users)  # hash kabhi bahar nahi


def test_admin_endpoints_non_admin_403(client):
    signup(client, email="admin@test.com")  # pehla = admin
    u2 = signup(client, email="user2@test.com")  # doosra = non-admin
    h2 = {"Authorization": f"Bearer {u2['data']['token']}"}
    assert u2["data"]["user"]["is_admin"] is False
    for method, path, kwargs in [
        ("get", "/api/v1/admin/users", {}),
        ("get", "/api/v1/admin/usage", {}),
        ("post", "/api/v1/admin/quota", {"json": {"user_id": 1, "cap": 10}}),
    ]:
        r = getattr(client, method)(path, headers=h2, **kwargs)
        assert r.status_code == 403, path
        assert r.json()["ok"] is False


def test_admin_no_token_401(client):
    r = client.get("/api/v1/admin/users")
    assert r.status_code == 401 and r.json()["ok"] is False


def test_admin_usage_report(client, auth_headers):
    headers, _ = auth_headers
    r = client.get("/api/v1/admin/usage", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and "day" in body["data"] and isinstance(body["data"]["rows"], list)


def test_admin_quota_override(client, auth_headers):
    headers, admin_data = auth_headers
    u2 = signup(client, email="capped@test.com")
    r = client.post("/api/v1/admin/quota", headers=headers,
                    json={"user_id": u2["data"]["user"]["id"], "cap": 10})
    assert r.status_code == 200 and r.json()["data"]["cap"] == 10
    h2 = {"Authorization": f"Bearer {u2['data']['token']}"}
    r = client.get("/api/v1/usage", headers=h2)
    assert r.json()["data"]["cap"] == 10  # per-user cap override lagoo


def test_admin_quota_unknown_user_404(client, auth_headers):
    headers, _ = auth_headers
    r = client.post("/api/v1/admin/quota", headers=headers, json={"user_id": 9999, "cap": 5})
    assert r.status_code == 404 and r.json()["ok"] is False


def test_auth_required_false_skips_token(client, monkeypatch):
    monkeypatch.setattr("config.settings.auth_required", False)
    r = client.post("/api/v1/ideas", json={"niche": "dog stories"})  # bina token
    assert r.status_code == 200 and r.json()["ok"] is True
    r = client.get("/api/v1/auth/me")  # dev user wapas
    assert r.status_code == 200 and r.json()["data"]["user"]["email"] == "dev@local"
