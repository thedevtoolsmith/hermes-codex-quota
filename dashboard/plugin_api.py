"""Read-only Codex quota for the focused live Desktop runtime (internal API)."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from contextlib import ExitStack
from datetime import datetime, timezone
from hashlib import sha256
from ipaddress import ip_address
import json
import math
import os
import re
import sys
import threading
import time

from fastapi import APIRouter, Request, Response
from agent import account_usage
from hermes_constants import profile_name_for_home

router = APIRouter()
_CACHE_TTL = 15.0
_CACHE_LIMIT = 64
_cache: OrderedDict = OrderedDict()
# Worker-only single flight: no event-loop blocking and no registry lock during I/O.
_cache_lock = threading.Lock()


def _unavailable(session_id: str, profile: str) -> dict:
    return {"available": False, "reason": "Quota unavailable", "session_id": session_id,
            "profile": profile, "provider": "openai-codex", "plan": None,
            "fetched_at": None, "windows": [], "details": []}


def _local_authenticated(request: Request) -> bool:
    # Do not import/start another web server or treat proxy headers as a local peer.
    web = sys.modules.get("hermes_cli.web_server")
    return bool(
        os.environ.get("HERMES_DESKTOP") == "1"
        and request.client and ip_address(request.client.host).is_loopback
        and getattr(request.state, "session", None) is None
        and not getattr(request.app.state, "auth_required", False)
        and web is not None and web._has_valid_session_token(request)
    )


def _fetch_live_codex_usage(*, base_url: str | None, api_key: str) -> account_usage.AccountUsageSnapshot:
    """One GET bound to the captured live token; never resolve, refresh or select auth.

    The general usage fetcher can fall back to another account after a 401.
    Let failures reach the generic route boundary; normal chat owns reauthentication.
    """
    payload = account_usage._get_json(
        account_usage._codex_backend_urls(base_url)[0],
        account_usage._codex_headers(api_key, None), timeout=15.0,
    )
    windows = account_usage._usage_windows(
        payload.get("rate_limit") or {}, (("primary_window", "Session"), ("secondary_window", "Weekly")),
        "used_percent", "reset_at",
    )
    details = []
    count = account_usage._codex_banked_resets(payload)
    if count > 0:
        details.append(f"You have {count} reset{'s' if count != 1 else ''} banked - use /usage reset to activate")
    credits = payload.get("credits") or {}
    balance = credits.get("balance")
    if credits.get("has_credits") and account_usage._is_num(balance):
        details.append(f"Credits balance: ${float(balance):.2f}")
    elif credits.get("has_credits") and credits.get("unlimited"):
        details.append("Credits balance: unlimited")
    return account_usage.AccountUsageSnapshot(
        provider="openai-codex", source="usage_api", fetched_at=datetime.now(timezone.utc),
        plan=account_usage._title_case_slug(payload.get("plan_type")),
        windows=tuple(windows), details=tuple(details),
    )


def _load(session_id: str, expected_profile: str) -> dict:
    with _cache_lock:
        return _load_serialized(session_id, expected_profile)


def _load_serialized(session_id: str, expected_profile: str) -> dict:
    server = sys.modules.get("tui_gateway.server")
    cache_key = (id(server), session_id)
    # Remove before validating, so failures/rotation cannot resurrect an older snapshot.
    cached = _cache.pop(cache_key, None)
    with ExitStack() as scope:
        with server._sessions_lock:
            session = server._sessions.get(session_id)
            if not session or session.get("_finalized"):
                return _unavailable(session_id, expected_profile)
            agent = session.get("agent")
            home = session.get("profile_home") or server._hermes_home
            if (profile_name_for_home(home) != expected_profile
                    or getattr(agent, "provider", None) != "openai-codex"):
                return _unavailable(session_id, expected_profile)
            key, base = getattr(agent, "api_key", None), getattr(agent, "base_url", None)
            model = getattr(agent, "model", None)
            if not isinstance(key, str) or not key.strip():
                return _unavailable(session_id, expected_profile)
            # Only an opaque credential/runtime digest lives in the cache, never a token,
            # agent/session object (which holds tokens), account ID or credential file.
            fingerprint = (id(session), id(agent), sha256(json.dumps(
                [key, base, model, str(home), expected_profile]).encode()).digest())
            if cached and cached[0] == fingerprint and time.monotonic() - cached[1] < _CACHE_TTL:
                _cache[cache_key] = cached
                return cached[2]
            # Enter while the captured home is still registry-bound; release before network I/O.
            scope.enter_context(server._session_profile_runtime_scope(session))
        snapshot = _fetch_live_codex_usage(base_url=base, api_key=key)
        result = _normalize(snapshot, session_id, expected_profile)
    with server._sessions_lock:
        if (sys.modules.get("tui_gateway.server") is not server
                or server._sessions.get(session_id) is not session
                or session.get("_finalized") or session.get("agent") is not agent
                or agent.provider != "openai-codex" or agent.api_key != key
                or getattr(agent, "model", None) != model
                or getattr(agent, "base_url", None) != base
                or (session.get("profile_home") or server._hermes_home) != home
                or profile_name_for_home(home) != expected_profile):
            return _unavailable(session_id, expected_profile)
        if result["available"]:
            _cache[cache_key] = (fingerprint, time.monotonic(), result)
            while len(_cache) > _CACHE_LIMIT:
                _cache.popitem(last=False)
        return result


def _date(value) -> str | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _percent(value) -> float | None:
    if type(value) not in (int, float) or not math.isfinite(value):
        return None
    return max(0.0, min(100.0, float(value)))


def _normalize(snapshot, session_id: str, profile: str) -> dict:
    result = _unavailable(session_id, profile)
    if (snapshot is None or snapshot.provider != "openai-codex"
            or not snapshot.available):
        return result
    windows = [{"label": w.label, "used_percent": _percent(w.used_percent),
                "reset_at": _date(w.reset_at)} for w in snapshot.windows[:8]
               if w.label in ("Session", "Weekly")]
    details = []
    # The helper emits only these Codex detail forms. Never pass arbitrary provider text,
    # URLs, account IDs, window.detail, source, title, or activation directions through.
    for text in snapshot.details[:8]:
        if not isinstance(text, str):
            continue
        banked = re.fullmatch(r"You have ([0-9]{1,9}) resets? banked - use /usage reset to activate", text)
        if banked:
            count = int(banked[1])
            details.append(f"{count} reset{'s' if count != 1 else ''} banked")
        elif re.fullmatch(r"Credits balance: (?:\$-?[0-9]{1,12}\.[0-9]{2}|unlimited)", text):
            details.append(text)
    if not details and not any(w["used_percent"] is not None or w["reset_at"] for w in windows):
        return result
    plans = ("Free", "Go", "Plus", "Pro", "Team", "Business", "Enterprise", "Edu", "Education")
    result.update(available=True, reason=None,
                  plan=snapshot.plan if snapshot.plan in plans else None,
                  fetched_at=_date(snapshot.fetched_at), windows=windows, details=details)
    return result


@router.get("/usage")
async def usage(request: Request, response: Response, session_id: str = "", expected_profile: str = "") -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        if session_id and expected_profile and _local_authenticated(request):
            return await asyncio.to_thread(_load, session_id, expected_profile)
    except Exception:
        # This boundary never emits/logs original exceptions, HTTP bodies or credentials.
        pass
    return _unavailable(session_id, expected_profile)
