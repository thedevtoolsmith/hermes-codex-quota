"""Isolated HTTP contract tests: synthetic credentials/snapshots, no live auth or network."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import sys
import threading
from types import ModuleType, SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

API_FILE = Path(__file__).resolve().parents[1] / "dashboard" / "plugin_api.py"
SID = "runtime-focused"
NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def env(monkeypatch, tmp_path, request):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_DESKTOP", "1")
    from agent import account_usage

    agent = SimpleNamespace(provider="openai-codex", api_key="synthetic-live-secret",
                            base_url="https://usage.invalid/backend-api/codex", model="test-model")
    session = {"agent": agent, "profile_home": str(tmp_path / "profiles" / "focused")}
    server = ModuleType("tui_gateway.server")
    server._sessions = {SID: session}
    server._sessions_lock = threading.RLock()
    server._hermes_home = tmp_path / "profiles" / "launch"
    scope_threads = []

    @contextmanager
    def scope(owner):
        assert owner is session
        scope_threads.append(threading.get_ident())
        yield

    server._session_profile_runtime_scope = scope
    monkeypatch.setitem(sys.modules, "tui_gateway.server", server)
    web = ModuleType("hermes_cli.web_server")
    web._has_valid_session_token = lambda request: request.headers.get("X-Test-Auth") == "local-test"
    monkeypatch.setitem(sys.modules, "hermes_cli.web_server", web)
    calls = []
    snapshot = account_usage.AccountUsageSnapshot(
        provider="openai-codex", source="usage_api", fetched_at=NOW, plan="Plus",
        windows=(account_usage.AccountUsageWindow("Session", 25.0, NOW),))

    def fetch(*, base_url, api_key):
        # Both fetch AND runtime scope must be outside the async event loop.
        with pytest.raises(RuntimeError, match="no running event loop"):
            asyncio.get_running_loop()
        assert scope_threads[-1] == threading.get_ident()
        calls.append((base_url, api_key))
        return state.snapshot

    # Load the actual standalone plugin exactly as the dashboard loader does.
    spec = importlib.util.spec_from_file_location("codex_quota_test_api", API_FILE)
    backend = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, backend)
    spec.loader.exec_module(backend)
    if getattr(request, "param", None) != "real-provider":
        monkeypatch.setattr(backend, "_fetch_live_codex_usage", fetch)
    app = FastAPI()
    app.state.auth_required = False
    request_state = {}

    @app.middleware("http")
    async def inject_auth_context(request, call_next):
        for key, value in request_state.items():
            setattr(request.state, key, value)
        return await call_next(request)

    app.include_router(backend.router, prefix="/api/plugins/codex-quota")
    state = SimpleNamespace(agent=agent, session=session, server=server, web=web, backend=backend,
                            snapshot=snapshot, calls=calls, app=app, request_state=request_state,
                            account_usage=account_usage, tmp_path=tmp_path, scope_threads=scope_threads)
    with TestClient(app, client=("127.0.0.1", 12000),
                    headers={"X-Test-Auth": "local-test"}) as client:
        state.client = client
        yield state


def get_usage(env, **params):
    return env.client.get("/api/plugins/codex-quota/usage",
                          params={"session_id": SID, "expected_profile": "focused", **params})


def test_live_focused_credentials_in_scoped_worker(env):
    response = get_usage(env)
    assert response.status_code == 200
    assert response.json() == {
        "available": True, "reason": None, "session_id": SID, "profile": "focused",
        "provider": "openai-codex", "plan": "Plus", "fetched_at": NOW.isoformat(),
        "windows": [{"label": "Session", "used_percent": 25.0, "reset_at": NOW.isoformat()}],
        "details": [],
    }
    assert env.calls == [(env.agent.base_url, env.agent.api_key)]
    assert env.agent.api_key not in response.text


def assert_unavailable(response):
    assert response.status_code == 200
    data = response.json()
    assert data["available"] is False
    assert data["reason"] == "Quota unavailable"
    assert data["windows"] == []
    assert data["details"] == []
    assert data["plan"] is None
    assert data["fetched_at"] is None
    return data


@pytest.mark.parametrize("env", ["real-provider"], indirect=True)
def test_http_401_never_resolves_refreshes_or_selects_credentials(env, monkeypatch, caplog):
    """Real helper chain: singleton 401 + AuthError must not expose another pool account."""
    from unittest.mock import Mock
    import httpx
    from agent import credential_pool

    usage = env.account_usage
    resolver = Mock(wraps=usage._resolve_codex_usage_credentials)
    tokens = Mock(return_value={"tokens": {"access_token": env.agent.api_key}})
    refresh = Mock(side_effect=usage.AuthError("synthetic refresh failure", provider="openai-codex"))
    pool = Mock()
    pool.select.return_value = SimpleNamespace(
        runtime_api_key="synthetic-other-account", runtime_base_url=env.agent.base_url)
    load_pool = Mock(return_value=pool)
    monkeypatch.setattr(usage, "_resolve_codex_usage_credentials", resolver)
    monkeypatch.setattr(usage, "_read_codex_tokens", tokens)
    monkeypatch.setattr(usage, "resolve_codex_runtime_credentials", refresh)
    monkeypatch.setattr(credential_pool, "load_pool", load_pool)
    requests = []

    def handle_request(self, request):
        with pytest.raises(RuntimeError, match="no running event loop"):
            asyncio.get_running_loop()
        assert env.scope_threads[-1] == threading.get_ident()
        requests.append(request)
        if request.headers["Authorization"] == f"Bearer {env.agent.api_key}":
            return httpx.Response(401, json={"error": "synthetic-live-secret account-id=private"})
        return httpx.Response(200, json={
            "plan_type": "pro", "rate_limit": {"primary_window": {"used_percent": 89}},
        })

    # Leave quota fetchers, GET helper, endpoint/header/window parsing real; no network.
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handle_request)
    response = get_usage(env)
    assert (response.json()["available"], resolver.call_count, refresh.call_count,
            pool.select.call_count) == (False, 0, 0, 0)
    assert_unavailable(response)
    tokens.assert_not_called()
    load_pool.assert_not_called()
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert str(requests[0].url) == "https://usage.invalid/backend-api/wham/usage"
    assert requests[0].headers["Authorization"] == f"Bearer {env.agent.api_key}"
    assert "ChatGPT-Account-Id" not in requests[0].headers
    assert env.backend._cache == {}
    assert all(text not in response.text + caplog.text
               for text in (env.agent.api_key, "synthetic-other-account", "account-id=private", "HTTP 401"))


@pytest.mark.parametrize("env", ["real-provider"], indirect=True)
@pytest.mark.parametrize("base,path,credits,count,plan,expected_plan,expected_credit", [
    ("https://usage.invalid/backend-api/codex", "/backend-api/wham/usage",
     {"has_credits": True, "balance": 12.5}, 1, "plus", "Plus", "Credits balance: $12.50"),
    ("https://usage.invalid/codex/", "/api/codex/usage",
     {"has_credits": True, "unlimited": True}, 2, "business", "Business", "Credits balance: unlimited"),
])
def test_live_get_preserves_quota_fields(env, monkeypatch, base, path, credits, count,
                                       plan, expected_plan, expected_credit):
    import httpx

    env.agent.base_url = base
    requests = []

    def handle_request(self, request):
        requests.append(request)
        return httpx.Response(200, json={
            "plan_type": plan,
            "rate_limit": {
                "primary_window": {"used_percent": 25, "reset_at": NOW.timestamp()},
                "secondary_window": {"used_percent": 80.5, "reset_at": NOW.isoformat()},
            },
            "rate_limit_reset_credits": {"available_count": count}, "credits": credits,
        })

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handle_request)
    response = get_usage(env)
    data = response.json()
    assert response.status_code == 200
    assert data["available"] is True and data["plan"] == expected_plan
    assert data["windows"] == [
        {"label": "Session", "used_percent": 25.0, "reset_at": NOW.isoformat()},
        {"label": "Weekly", "used_percent": 80.5, "reset_at": NOW.isoformat()},
    ]
    assert data["details"] == [f"{count} reset{'s' if count != 1 else ''} banked", expected_credit]
    assert datetime.fromisoformat(data["fetched_at"]).utcoffset().total_seconds() == 0
    assert len(requests) == 1
    assert requests[0].method == "GET" and str(requests[0].url) == f"https://usage.invalid{path}"
    assert requests[0].headers["Authorization"] == f"Bearer {env.agent.api_key}"
    assert requests[0].headers["Accept"] == "application/json"
    assert requests[0].headers["User-Agent"] == "codex-cli"
    assert "ChatGPT-Account-Id" not in requests[0].headers
    assert set(requests[0].extensions["timeout"].values()) == {15.0}
    assert env.agent.api_key not in response.text
    assert "/usage reset" not in response.text


@pytest.mark.parametrize("failure", [
    "wrong-profile", "missing-session", "durable-id", "finalized", "missing-agent",
    "other-provider", "empty-key", "blank-key", "nonstring-key", "no-server",
    "unknown-home", "missing-profile", "missing-sid",
])
def test_unbound_or_unusable_runtime_never_fetches(env, monkeypatch, failure):
    params = {}
    if failure == "wrong-profile":
        params["expected_profile"] = "launch"
    elif failure == "missing-session":
        env.server._sessions.clear()
    elif failure == "durable-id":
        env.session["session_key"] = "durable-123"
        params["session_id"] = "durable-123"
    elif failure == "finalized":
        env.session["_finalized"] = True
    elif failure == "missing-agent":
        env.session["agent"] = None
    elif failure == "other-provider":
        env.agent.provider = "openrouter"
    elif failure.endswith("key"):
        env.agent.api_key = {"empty-key": "", "blank-key": "  ", "nonstring-key": 42}[failure]
    elif failure == "no-server":
        monkeypatch.delitem(sys.modules, "tui_gateway.server")
    elif failure == "unknown-home":
        env.session["profile_home"] = str(env.tmp_path / "arbitrary")
    elif failure == "missing-profile":
        params["expected_profile"] = ""
    elif failure == "missing-sid":
        params["session_id"] = ""
    assert_unavailable(get_usage(env, **params))
    assert env.calls == []


@pytest.mark.parametrize("host,state,authenticated,gated", [
    ("203.0.113.4", {}, True, False),
    ("localhost", {}, True, False),
    ("127.0.0.1", {"session": {}}, True, False),
    ("127.0.0.1", {"session": SimpleNamespace(user="oauth")}, True, False),
    ("127.0.0.1", {}, False, False),
    ("127.0.0.1", {}, True, True),
    ("127.0.0.1", {"token_authenticated": True}, False, False),
])
def test_only_authenticated_local_desktop_requests_fetch(env, host, state, authenticated, gated):
    env.request_state.update(state)
    env.app.state.auth_required = gated
    with TestClient(env.app, client=(host, 12000),
                    headers={"X-Test-Auth": "local-test"} if authenticated else {}) as client:
        response = client.get("/api/plugins/codex-quota/usage",
                              params={"session_id": SID, "expected_profile": "focused"},
                              headers={"X-Forwarded-For": "127.0.0.1"})
    assert_unavailable(response)
    assert env.calls == []


@pytest.mark.parametrize("change", ["key", "provider", "model", "base", "home", "agent", "session", "finalized", "server"])
def test_runtime_changes_inflight_discard_snapshot(env, monkeypatch, change):
    from concurrent.futures import ThreadPoolExecutor
    entered, release = threading.Event(), threading.Event()

    def fetch(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return env.snapshot

    monkeypatch.setattr(env.backend, "_fetch_live_codex_usage", fetch)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(get_usage, env)
        try:
            assert entered.wait(5)
            # The server registry lock must remain available during provider I/O.
            assert env.server._sessions_lock.acquire(timeout=1)
            try:
                if change == "key":
                    env.agent.api_key = "synthetic-rotated-secret"
                elif change == "provider":
                    env.agent.provider = "other"
                elif change == "model":
                    env.agent.model = "other-model"
                elif change == "base":
                    env.agent.base_url = "https://different.invalid"
                elif change == "home":
                    env.session["profile_home"] = str(env.tmp_path / "different" / "profiles" / "focused")
                elif change == "agent":
                    env.session["agent"] = SimpleNamespace(**vars(env.agent))
                elif change == "session":
                    env.server._sessions[SID] = dict(env.session)
                elif change == "finalized":
                    env.session["_finalized"] = True
                else:
                    monkeypatch.setitem(sys.modules, "tui_gateway.server", ModuleType("replacement"))
            finally:
                env.server._sessions_lock.release()
        finally:
            release.set()
        assert_unavailable(pending.result(timeout=5))


def test_serializes_only_allowlisted_finite_quota_fields(env):
    from dataclasses import replace
    Window = env.account_usage.AccountUsageWindow
    env.snapshot = replace(env.snapshot, plan="sk-secret account-id", windows=(
        Window("Session", float("nan"), NOW), Window("Weekly", float("inf")),
        Window("Session", True), Window("Weekly", -2), Window("Session", 140),
        Window("secret-account-id", 50),
    ), details=("Credits balance: $12.50", "Credits balance: unlimited",
                "You have 2 resets banked - use /usage reset to activate",
                "Credits balance: $nan", "account-id=secret-account-id", env.agent.api_key))
    response = get_usage(env)
    assert response.status_code == 200
    data = response.json()
    assert data["available"] is True
    assert data["plan"] is None
    assert [w["used_percent"] for w in data["windows"]] == [None, None, None, 0.0, 100.0]
    assert data["details"] == ["Credits balance: $12.50", "Credits balance: unlimited", "2 resets banked"]
    assert all(text not in response.text for text in ("secret", "account-id", "NaN", "Infinity", "/usage reset"))


@pytest.mark.parametrize("bad", ["none", "empty", "unavailable", "wrong-provider", "invalid-only", "exception"])
def test_provider_failures_have_generic_serializable_response(env, monkeypatch, caplog, bad):
    from dataclasses import replace
    if bad == "none":
        env.snapshot = None
    elif bad == "empty":
        env.snapshot = replace(env.snapshot, windows=())
    elif bad == "unavailable":
        env.snapshot = replace(env.snapshot, unavailable_reason="HTTP 401 Bearer synthetic-live-secret")
    elif bad == "wrong-provider":
        env.snapshot = replace(env.snapshot, provider="other")
    elif bad == "invalid-only":
        env.snapshot = replace(env.snapshot, windows=(env.account_usage.AccountUsageWindow("Session", float("nan")),))
    else:
        def fail(*args, **kwargs):
            raise RuntimeError("HTTP 401 Bearer synthetic-live-secret account-id=private")
        monkeypatch.setattr(env.backend, "_fetch_live_codex_usage", fail)
    response = get_usage(env)
    data = assert_unavailable(response)
    assert data["session_id"] == SID and data["profile"] == "focused"
    assert "synthetic-live-secret" not in response.text + caplog.text
    assert "HTTP 401" not in response.text + caplog.text


def test_success_cache_expires_after_fifteen_seconds(env):
    import time
    first = get_usage(env).json()
    assert get_usage(env).json() == first
    assert len(env.calls) == 1
    time.sleep(15.05)
    assert get_usage(env).json()["available"] is True
    assert len(env.calls) == 2


def test_cache_is_credential_scoped_and_does_not_restore_old_account(env):
    assert get_usage(env).json()["available"] is True
    old_key = env.agent.api_key
    env.agent.api_key = "synthetic-next-account"
    env.snapshot = None
    assert_unavailable(get_usage(env))
    assert_unavailable(get_usage(env))
    env.agent.api_key = old_key
    assert_unavailable(get_usage(env))
    assert len(env.calls) == 4  # No cached failure and no old-account resurrection.


def test_cache_is_runtime_scoped_and_bounded(env):
    for index in range(65):
        sid = f"runtime-{index}"
        env.server._sessions[sid] = env.session
        assert get_usage(env, session_id=sid).json()["session_id"] == sid
    assert len(env.calls) == 65
    assert len(env.backend._cache) <= 64
    get_usage(env, session_id="runtime-64")
    assert len(env.calls) == 65
    get_usage(env, session_id="runtime-0")
    assert len(env.calls) == 66
    assert env.agent.api_key not in repr(env.backend._cache)


@pytest.mark.parametrize("change", ["profile", "provider", "key", "finalized"])
def test_cache_hits_revalidate_binding(env, change):
    assert get_usage(env).json()["available"] is True
    if change == "profile":
        env.session["profile_home"] = str(env.tmp_path / "profiles" / "other")
    elif change == "provider":
        env.agent.provider = "other"
    elif change == "key":
        env.agent.api_key = ""
    else:
        env.session["_finalized"] = True
    assert_unavailable(get_usage(env))
    assert len(env.calls) == 1


@pytest.mark.parametrize("default", [False, True])
def test_launch_profile_uses_authoritative_home(env, default):
    env.session["profile_home"] = None
    if default:
        env.server._hermes_home = env.tmp_path
    profile = "default" if default else "launch"
    data = get_usage(env, expected_profile=profile).json()
    assert data["available"] is True and data["profile"] == profile


def test_ipv6_loopback_with_local_auth_is_supported(env):
    with TestClient(env.app, client=("::1", 12000), headers={"X-Test-Auth": "local-test"}) as client:
        assert client.get("/api/plugins/codex-quota/usage",
                          params={"session_id": SID, "expected_profile": "focused"}).json()["available"] is True


def test_local_non_desktop_process_is_unsupported(env, monkeypatch):
    monkeypatch.delenv("HERMES_DESKTOP")
    assert_unavailable(get_usage(env))
    assert env.calls == []


def test_quota_response_is_not_http_cacheable(env):
    assert get_usage(env).headers.get("Cache-Control") == "no-store"
