# Observability
<!-- .claude/rules/project/005-observability.md -- Sentry, breadcrumbs, releases -->

Sentry initialization lives in `common/src/main.py`. Detailed conventions live in `docs/observability.md`; update that doc when changing behavior described there.

## Environment

| Variable | Purpose |
|----------|---------|
| `SENTRY_DSN` | Enables reporting when set. Unset locally to disable reporting. |
| `SENTRY_ENVIRONMENT` | Runtime environment; use `development` for local runs. |
| `SENTRY_RELEASE` | Release identifier; Nix images default this to the flake revision. |

`common/src/main.py` falls back from `SENTRY_RELEASE` to `GIT_SHA` to `dev` for compatibility. Do not make Nix evaluation depend on `GIT_SHA`.

## Breadcrumbs

Add `sentry_sdk.add_breadcrumb()` for important background tasks and decision points, especially around downloader handling, table generation, LLM calls, and scheduled jobs.

Breadcrumbs should capture useful context without secrets. Do not log Telegram tokens, API keys, session strings, or full secret-bearing config values.

<!-- sentry-verified -->
