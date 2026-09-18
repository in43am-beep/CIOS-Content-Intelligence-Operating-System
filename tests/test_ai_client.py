"""Real ai_client.generate() path — httpx is faked, so no network, no key, $0.

Covers: MissingApiKey up front, 4xx fallback chain, attempt-based quota,
atomic cap enforcement, per-user cache namespacing, cache-before-quota.
"""
import json

import httpx
import pytest

import ai_client
from config import settings
from database import create_user, get_conn, init_db, set_user_cap, usage_for_user

MODELS = settings.model_chain


class FakeResp:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text else (json.dumps(payload) if payload else "")

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def ok_text(s='{"ok": true}'):
    return FakeResp(200, {"choices": [{"message": {"content": s}}]})


class FakeHTTP:
    """Drop-in for ai_client._client: handler(model) -> FakeResp. Records calls."""

    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def post(self, url, headers=None, json=None):
        model = json["model"]
        self.calls.append(model)
        # API key header kabhi leak nahi honi chahiye test me bhi — sanity:
        assert headers["Authorization"].startswith("Bearer ")
        return self.handler(model)


@pytest.fixture()
def db(monkeypatch, tmp_path):
    monkeypatch.setattr("config.settings.db_path", str(tmp_path / "test.db"))
    monkeypatch.setattr("config.settings.openrouter_api_key", "dummy-key")
    monkeypatch.setattr("config.settings.min_seconds_between_calls", 0.0)
    init_db()
    return tmp_path


def use_http(monkeypatch, handler):
    fake = FakeHTTP(handler)
    monkeypatch.setattr(ai_client, "_client", fake)
    return fake


def test_missing_key_checked_once_upfront(db, monkeypatch):
    monkeypatch.setattr("config.settings.openrouter_api_key", "")
    fake = use_http(monkeypatch, lambda m: ok_text())
    with pytest.raises(ai_client.MissingApiKey):
        ai_client.generate("sys", "hello", user_id=0)
    assert fake.calls == []  # koi outbound request nahi hui


def test_fallback_404_then_model2_serves(db, monkeypatch):
    def handler(model):
        if model == MODELS[0]:
            return FakeResp(404, text='{"error": "model not found"}')
        return ok_text('{"served": "by-model-2"}')

    fake = use_http(monkeypatch, handler)
    res = ai_client.generate("sys", "hello", user_id=5)
    assert res["model"] == MODELS[1] and res["cached"] is False
    assert fake.calls == [MODELS[0], MODELS[1]]  # 404 ne chain nahi tori
    # Attempt-based quota: dono attempts gin'ti hue (audit B-3)
    assert usage_for_user(5)["used"] == 2


def test_all_models_fail_raises_friendly(db, monkeypatch):
    fake = use_http(monkeypatch, lambda m: FakeResp(400, text='{"error": "SECRET-MARKER-XYZ"}'))
    with pytest.raises(ai_client.AllModelsFailed) as ei:
        ai_client.generate("sys", "hello", user_id=6)
    assert "Saare free models fail" in str(ei.value)
    assert "SECRET-MARKER-XYZ" not in str(ei.value)  # raw body client ko nahi
    assert fake.calls == MODELS  # poori chain try hui
    assert usage_for_user(6)["used"] == len(MODELS)  # har attempt gina gaya


def test_quota_cap_enforced_atomically(db, monkeypatch):
    use_http(monkeypatch, lambda m: ok_text())
    user = create_user("q@test.com", "x", False, 48)
    set_user_cap(user["id"], 1)
    uid = user["id"]
    ai_client.generate("sys", "one", user_id=uid)  # attempt 1 ok
    with pytest.raises(ai_client.QuotaExceeded):
        ai_client.generate("sys", "two", user_id=uid)  # cap full -> 0 rowcount
    assert usage_for_user(uid)["used"] == 1
    assert usage_for_user(uid)["cap"] == 1


def test_cache_hit_skips_provider_and_quota(db, monkeypatch):
    fake = use_http(monkeypatch, lambda m: ok_text('{"n": 1}'))
    r1 = ai_client.generate("sys", "same prompt", user_id=7)
    r2 = ai_client.generate("sys", "same prompt", user_id=7)
    assert r1["cached"] is False and r2["cached"] is True
    assert r2["model"] == "cache"
    assert len(fake.calls) == 1  # doosri dafa koi outbound call nahi
    assert usage_for_user(7)["used"] == 1  # cache hit quota nahi khata


def test_cache_is_per_user_namespaced(db, monkeypatch):
    fake = use_http(monkeypatch, lambda m: ok_text('{"n": 1}'))
    ai_client.generate("sys", "same prompt", user_id=11)
    r = ai_client.generate("sys", "same prompt", user_id=12)  # doosra user -> miss
    assert r["cached"] is False
    assert len(fake.calls) == 2


def test_cache_checked_before_quota(db, monkeypatch):
    fake = use_http(monkeypatch, lambda m: ok_text('{"n": 1}'))
    ai_client.generate("sys", "prompt", user_id=13)  # cache prime
    monkeypatch.setattr("config.settings.daily_request_cap", 0)  # cap khatm
    r = ai_client.generate("sys", "prompt", user_id=13)  # cache hit -> quota se pehle
    assert r["cached"] is True  # QuotaExceeded nahi hua


def test_pace_updates_last_call_at(db, monkeypatch):
    use_http(monkeypatch, lambda m: ok_text())
    ai_client.generate("sys", "hello", user_id=14)
    conn = get_conn()
    try:
        row = conn.execute("SELECT last_call_at FROM rate_state WHERE id=1").fetchone()
        assert row["last_call_at"] > 0  # monotonic slot reserve hua
    finally:
        conn.close()


def test_retryable_500_also_falls_through_chain(db, monkeypatch):
    def handler(model):
        if model in (MODELS[0], MODELS[1]):
            return FakeResp(500, text="boom")
        return ok_text('{"via": "third"}')

    fake = use_http(monkeypatch, handler)
    res = ai_client.generate("sys", "hello", user_id=15)
    assert res["model"] == MODELS[2]
    assert fake.calls == MODELS[:3]


# ------------------------------------------------- defensive JSON parsing ---
def test_malformed_200_shapes_skip_to_next_model(db, monkeypatch):
    # openrouter/free kabhi 200 ke sath ghalat shape bhejta hai.
    def handler(model):
        if model == MODELS[0]:
            return FakeResp(200, payload=["not", "a", "dict"])  # non-dict top level
        if model == MODELS[1]:
            return FakeResp(200, payload={"choices": [{"message": {"content": 12345}}]})
        return ok_text('{"fine": true}')

    fake = use_http(monkeypatch, handler)
    res = ai_client.generate("sys", "hello", user_id=21)
    assert res["model"] == MODELS[2]  # dono malformed models skip hue
    assert fake.calls == MODELS[:3]


def test_empty_choices_skipped(db, monkeypatch):
    def handler(model):
        if model == MODELS[0]:
            return FakeResp(200, payload={"choices": []})
        return ok_text('{"fine": true}')

    fake = use_http(monkeypatch, handler)
    res = ai_client.generate("sys", "hello", user_id=22)
    assert res["model"] == MODELS[1]
    assert fake.calls == MODELS[:2]


# ------------------------------------------------------- proxy handling ---
PROXY_VARS = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy")


def test_proxy_url_picks_https_first(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://user:pass@proxy.example:3128")
    monkeypatch.setenv("https_proxy", "http://other:1@x.example:8080")
    assert ai_client._proxy_url() == "http://user:pass@proxy.example:3128"


def test_proxy_url_skips_garbage_values(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "not-a-url")
    monkeypatch.setenv("https_proxy", "")
    monkeypatch.setenv("HTTP_PROXY", "ftp://x.example:21")  # ghalat scheme
    monkeypatch.setenv("http_proxy", "http://valid:2@h.example:8080")
    assert ai_client._proxy_url() == "http://valid:2@h.example:8080"


def test_proxy_url_none_when_no_valid_proxy(monkeypatch):
    for v in PROXY_VARS:
        monkeypatch.delenv(v, raising=False)
    assert ai_client._proxy_url() is None


def test_http_client_constructs_with_bracketed_no_proxy(monkeypatch):
    # Regression: httpx 0.28.1 crashes Client() build when NO_PROXY contains
    # bracketed IPv6 ('[::1]' -> InvalidURL). trust_env=False + explicit proxy
    # bypasses that parsing entirely.
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1,::1,[::1],198.19.0.1")
    monkeypatch.setenv("no_proxy", "localhost,[::1]")
    monkeypatch.setenv("HTTPS_PROXY", "http://u:p@hatch-egress-proxy:3128")
    monkeypatch.setattr(ai_client, "_client", None)
    try:
        client = ai_client._http()  # must not raise
        assert isinstance(client, httpx.Client)
    finally:
        monkeypatch.setattr(ai_client, "_client", None)  # global reset
