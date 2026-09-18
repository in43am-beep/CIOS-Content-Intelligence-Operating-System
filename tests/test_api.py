"""CIOS API tests — AI calls are mocked, so tests cost $0 and need no API key."""
import json

import pytest
from fastapi.testclient import TestClient

import ai_client
import app as app_module
from database import init_db

FAKE = {"text": '{"ideas":[{"title":"Test","angle":"A","score":25,"why":"ok"}]}',
        "model": "test-model", "cached": False}


@pytest.fixture()
def client(monkeypatch, tmp_path):
    # Har test apne temp DB pe chale — asli cios.db clean rahe.
    monkeypatch.setattr("config.settings.db_path", str(tmp_path / "test.db"))
    init_db()
    monkeypatch.setattr(ai_client, "generate", lambda *a, **k: dict(FAKE))
    return TestClient(app_module.app)


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert isinstance(body["models"], list) and body["models"]


def test_ideas(client):
    r = client.post("/api/ideas", json={"niche": "dog stories", "audience": "seniors", "count": 5})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["model"] == "test-model"
    assert body["data"]["ideas"][0]["title"] == "Test"


def test_ideas_validation(client):
    r = client.post("/api/ideas", json={"niche": "x"})  # too short
    assert r.status_code == 422


def test_research(client):
    r = client.post("/api/research", json={"niche": "dog stories"})
    assert r.status_code == 200 and r.json()["ok"] is True


def test_scripts(client):
    r = client.post("/api/scripts", json={"topic": "a lost puppy", "duration_sec": 60})
    assert r.status_code == 200 and r.json()["ok"] is True


def test_packaging(client):
    r = client.post("/api/packaging", json={"topic": "a lost puppy"})
    assert r.status_code == 200 and r.json()["ok"] is True


def test_seo(client):
    r = client.post("/api/seo", json={"title": "Lost Puppy Found", "topic": "puppy rescue"})
    assert r.status_code == 200 and r.json()["ok"] is True


def test_niche(client):
    r = client.post("/api/niche", json={"niche": "AI history documentaries"})
    assert r.status_code == 200 and r.json()["ok"] is True


def test_history_records(client):
    client.post("/api/ideas", json={"niche": "dog stories"})
    r = client.get("/api/history?limit=5")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) >= 1 and items[0]["feature"] == "ideas"


def test_quota_exceeded_maps_to_429(client, monkeypatch):
    def boom(*a, **k):
        raise ai_client.QuotaExceeded("limit poori")
    monkeypatch.setattr(ai_client, "generate", boom)
    r = client.post("/api/ideas", json={"niche": "dog stories"})
    assert r.status_code == 429
    assert "limit" in r.json()["error"]


def test_raw_text_fallback(client, monkeypatch):
    monkeypatch.setattr(ai_client, "generate",
                        lambda *a, **k: {"text": "yeh plain jawab hai", "model": "m", "cached": False})
    r = client.post("/api/ideas", json={"niche": "dog stories"})
    assert r.status_code == 200
    assert r.json()["data"]["text"] == "yeh plain jawab hai"


def test_usage_endpoint(client):
    r = client.get("/api/usage")
    assert r.status_code == 200
    assert "used" in r.json() and "cap" in r.json()


def test_missing_key_maps_to_400(client, monkeypatch):
    def boom(*a, **k):
        raise ai_client.MissingApiKey("key set nahi")
    monkeypatch.setattr(ai_client, "generate", boom)
    r = client.post("/api/ideas", json={"niche": "dog stories"})
    assert r.status_code == 400
    assert "key" in r.json()["error"]
