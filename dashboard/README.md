# Codex quota backend (internal v1)

`GET /api/plugins/codex-quota/usage?session_id=<runtime-id>&expected_profile=<focused-profile>`

Exports `router = APIRouter()`; the host mounts and authenticates it. The plugin
also requires `HERMES_DESKTOP=1`, a numeric loopback peer, the existing local
session-token verifier, no OAuth/basic-auth `request.state.session`, and no
`auth_required` gate. Remote/multiuser use is intentionally unsupported. Forwarded
IP headers do not authorize a caller. This does not independently prove that the
caller is Electron: possession of the Desktop token is the local trust boundary.

Only the exact live runtime in the already-imported `tui_gateway.server._sessions`
is eligible. The authoritative `session.profile_home` or server launch home must
resolve through `hermes_constants.profile_name_for_home` to the requested profile.
The plugin never imports/starts the server or resolves default/pool credentials.

The private `_fetch_live_codex_usage` performs one GET using the exact live
agent's Codex token and base URL inside `_session_profile_runtime_scope(session)`,
on a worker thread. It reuses Hermes's endpoint, header and quota-parsing helpers,
but never calls the general `fetch_account_usage` or any credential resolver,
refresh or pool selector. A 401 fails closed to generic unavailable; normal chat
reauthentication owns credential refresh. The registry lock is released for
network I/O. Session/agent identity, provider, credential, model, base URL, and
home are revalidated before returning data.

Only successful normalized snapshots are cached, for 15 seconds (maximum 64
runtime/account-bound entries). Cache identity contains a credential/runtime
digest, not tokens or agent objects. Changes invalidate the prior entry; failures
never restore it. One worker-side lock coalesces fetches, so unrelated quota reads
can queue behind a slow provider request. HTTP responses use `Cache-Control:
no-store`. All failures return a generic unavailable envelope with the requested
session/profile echoed, not original exceptions. Only known quota labels, plans,
finite percentages, timestamps and allowlisted helper details are exposed.
Banked resets are read-only counts; there is no redemption route or instruction.

## Verification

Run using the Hermes venv Python, with repository imports available:

```sh
PYTHONPATH=/path/to/hermes-agent /path/to/hermes-python -m pytest -q tests/test_backend.py
ruff check --no-cache .
```

Tests exercise the real FastAPI route using TestClient. They supply isolated
registry/auth stubs and synthetic provider snapshots, monkeypatch the local
quota wrapper after plugin loading, and set HERMES_HOME to a temporary directory.
Integration cases leave that wrapper and Hermes's GET/parsing helpers real,
replacing only HTTP transport with synthetic responses. The 401 regression
supplies synthetic singleton/refresh/pool stubs to reproduce the former fallback
and asserts that credential resolution, refresh, token reads and pool selection
are never called. Success cases verify both endpoint layouts, windows/resets,
plans, banked counts and numeric/unlimited credits. Tests do not read real
authentication state or fetch live quotas. The expiry test intentionally waits
15 seconds rather than replacing the clock.

## Compatibility seams reviewed

- `tui_gateway/server.py`: `_sessions`, `_sessions_lock`, `_hermes_home`.
- `tui_gateway/model_switch.py`: `_session_profile_runtime_scope`, installed into
  the server namespace; provider/model/key/base are the agent runtime fields.
- `tui_gateway/session_lifecycle.py`: `_finalized` marks unavailable runtimes.
- `hermes_constants.py`: `profile_name_for_home` authoritative-home resolver.
- `hermes_cli/web_server.py`: `_has_valid_session_token`, `auth_required` and
  Desktop loopback authentication contract.
- `agent/account_usage.py`: immutable usage snapshots and private GET, endpoint,
  header and parsing helpers only. The general fetcher's 401 credential fallback
  is intentionally bypassed to keep every quota read bound to the live account.

These are private seams, not a stable public API. Missing/incompatible seams fail
closed. Tests cannot prove live Desktop mounting or real provider responses;
installation/enablement and live verification remain separate operator steps.
