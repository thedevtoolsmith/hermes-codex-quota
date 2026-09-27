# Codex Quota for Hermes Desktop

> **This plugin is fully vibecoded.** It was built entirely with AI assistance through Hermes Agent. It is experimental: automated tests are included, but they are not a substitute for an independent security audit. Review the code before trusting it with your local Hermes runtime.

An always-visible Codex subscription allowance indicator in the bottom-right status bar of Hermes Desktop. Click it for reset times, reported quota windows, banked reset counts, and a manual refresh.

Standalone plugin. No ResetWatch dependency, browser scraping, model calls, or Hermes core patches.

## Install

Requires a compatible Hermes Desktop installation and a local Hermes chat signed in with the `openai-codex` provider.

```sh
hermes plugins install thedevtoolsmith/hermes-codex-quota --enable
```

1. Quit and reopen Hermes Desktop once so its backend loads the new API routes.
2. In **Capabilities → Plugins**, enable the **Codex Quota** desktop component if it is off. Python backend enablement and the desktop toggle are separate.
3. Select a local Codex chat. Send a message if its agent runtime has not initialized.
4. Look for `Codex · …% left` at the bottom-right. Click it for details or **Refresh**.

If the UI is missing, use **⌘K → Reload desktop plugins**. Other profiles need their own explicit backend installation/enablement; installing in one profile does not change the others. Authenticate separately on each machine—credentials are never part of this repository.

### Existing manually installed copy

Do not keep a standalone desktop copy and a packaged copy with the same plugin ID. Back up the old `plugins/codex-quota` and `desktop-plugins/codex-quota` folders outside Hermes's discovery directories before migrating to this package, then install normally. The existing running chat need not be interrupted until you are ready to restart.

### Updates

```sh
hermes plugins update codex-quota
```

Restart the desktop after backend changes. This is a direct-repository installation, not an officially reviewed Hermes catalog entry.

## Behavior

- The compact chip shows the **first reported quota window**, not a combined allowance or context-window percentage.
- The popover shows all returned supported windows and reset timestamps in your local time.
- Automatic refresh every three minutes while the app is active; manual Refresh is available. The backend may reuse a successful reading for up to fifteen seconds.
- Missing usage is **unavailable**, not zero. Failed refreshes mark an older reading **stale**.
- It never invents a weekly or five-hour allowance when the provider does not return one.

## Scope and compatibility

This version supports **local Hermes Desktop sessions with an initialized in-process Codex agent**. It does not support remote/SSH/cloud backends, compute-host sessions, CLI status bars, or an overall ChatGPT message allowance. The focused chat and active workspace must belong to the same local profile.

The plugin depends on internal Hermes session and account-usage interfaces; compatibility is not guaranteed across Hermes versions. No cross-platform desktop testing has been completed. An incompatible or unbound runtime returns unavailable rather than choosing another account.

## Privacy and safety

- Uses the exact live chat agent's credential, only in the backend.
- Makes one GET-only quota request using Hermes's endpoint/header/parser helpers.
- Does not resolve, refresh, rotate, or select credentials. An expired token returns unavailable until the normal chat login/refresh path repairs it.
- Does not redeem banked resets, buy credits, or change your subscription.
- Returns normalized quota fields, not credentials or raw provider errors, to the renderer.
- Keeps a small short-lived in-memory cache; does not persist quota or send telemetry to third parties.

The outbound destination is derived from the agent's configured Codex base URL; use a trusted provider configuration.

## Package layout

```text
plugin.yaml
__init__.py
dashboard/
  manifest.json
  plugin_api.py
desktop/
  plugin.js
tests/
```

Hermes installs the backend package and discovers its bundled desktop component. No manual copying of JavaScript is needed on a fresh installation.

## Development and tests

The tests require a Hermes source checkout with its Python environment and desktop Node dependencies installed. The Node loader uses `registerHooks`; use a Node version providing that API (tested with Node 26).

From this repository:

```sh
HERMES_SOURCE=/path/to/hermes-agent node --import ./tests/register.mjs --test ./tests/quota-ui.test.mjs ./tests/desktop.test.mjs
PYTHONPATH=/path/to/hermes-agent /path/to/hermes-python -m pytest -q tests/test_backend.py
hermes plugins doctor . --ci
```

Tests use labeled synthetic fixtures and intercepted HTTP transport, never real credentials. Coverage includes missing data, refresh failures, profile/session binding, credential changes during requests, authentication gates, normalization, cache behavior, and rejecting HTTP 401 without switching accounts.

## Remove

Disable **Codex Quota** in the desktop Plugins page, remove the `codex-quota` plugin through Hermes, and restart to unload the backend routes. No core files need restoration.
