# Observability

Error reporting, performance traces, and breadcrumbs via [Sentry](https://de.sentry.io).

**Sentry project:** `kittyandrew/vtraty-pes-bot` on `https://de.sentry.io`

## Setup

`SENTRY_DSN` must be set as an environment variable. When unset, the SDK initializes as a no-op.

| Variable | Purpose | Where set |
|----------|---------|-----------|
| `SENTRY_DSN` | Sentry ingest endpoint | Runtime env. Unset locally to disable reporting. |
| `SENTRY_ENVIRONMENT` | `development` / `production` | Runtime env; Nix images default to `production`. |
| `SENTRY_RELEASE` | Release identifier for Sentry | Runtime env; Nix images default to the flake revision. |

### Local dev

Use shell env vars when running locally. `SENTRY_ENVIRONMENT=development` prevents local errors from triggering production alerts.

```bash
SENTRY_ENVIRONMENT=development nix develop --command uv run vtraty-pes-bot --config data/local-dev/pes/config.ini
```

### Docker image

`SENTRY_ENVIRONMENT=production` and `SENTRY_RELEASE` are set in the Nix-built images (`flake.nix` → `config.Env`). `SENTRY_DSN` is passed at runtime; it is **not** baked in.

## What's instrumented

### Init (`__init__.py`)

`sentry_sdk.init()` runs once at startup in `_main()`, after logging setup. Configures:
- `traces_sample_rate=1.0` (all transactions traced)
- `environment` fallback chain: `SENTRY_ENVIRONMENT` → `HOSTNAME` → `platform.node()`
- `release` from `SENTRY_RELEASE`, then `GIT_SHA`, then `"dev"`

### Breadcrumbs

Breadcrumbs are logged at critical decision points so Sentry events include context:

| Location | Category | What it captures |
|----------|----------|-----------------|
| `tmodules/table.py` — `generate_table()` | `table` | Number of posts being processed for a date |
| `tmodules/table.py` — `scheduled_table()` | `schedule` | Start of scheduled table generation |
| `llm.py` — `parse_messages()` | `llm` | Number of messages sent to OpenAI |
| `tmodules/downloader.py` — handler | `downloader` | Platform and URL of video being downloaded |

### Auto-captured by SDK

The Python SDK auto-captures:
- Unhandled exceptions (any `raise` that propagates to the event loop)
- `logging.error()` / `logging.exception()` calls (via logging integration)
- HTTP client breadcrumbs from `aiohttp` sessions

## Adding new instrumentation

### Breadcrumbs (lightweight context)

```python
import sentry_sdk

sentry_sdk.add_breadcrumb(
    category="my_module",
    message="Descriptive message",
    data={"key": "value"},  # optional structured data
)
```

Add these before operations that might fail — they show up in the event's breadcrumb trail.

### Manual error capture

For errors you catch and handle but still want Sentry to know about:

```python
try:
    risky_operation()
except SomeExpectedError:
    sentry_sdk.capture_exception()
    # continue with fallback
```

## Alert rules

| Rule | Trigger | Action |
|------|---------|--------|
| Create GitHub issue on new errors | First occurrence of a new issue (production only) | Creates a GitHub issue labeled `sentry` |

The production filter prevents development errors from creating issues.

## Debugging

**Sentry not receiving events?**
1. Check `SENTRY_DSN` is set: `echo $SENTRY_DSN`
2. Check environment: events may be filtered by alert rules (production only)
3. Send a test event: `nix shell nixpkgs#sentry-cli -c bash -c 'SENTRY_DSN=... sentry-cli send-event -m "test"'`
4. Check the Sentry web UI for rate limits or quota issues
