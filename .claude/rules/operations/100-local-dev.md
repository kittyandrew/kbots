# Local Dev Bot
<!-- .claude/rules/operations/100-local-dev.md -- setup, login, run, teardown -->

Do not use container orchestration for local bot runs. Use the uv workspace through `nix develop`, keep disposable state under `data/local-dev/`, and do not contact Telegram or create login sessions unless the user explicitly asks for a real local run.

## Setup

```bash
nix develop --command uv sync --frozen --all-packages
mkdir -p data/local-dev/pes data/local-dev/admin
cp pes/config.ini.sample data/local-dev/pes/config.ini
cp admin/config.ini.sample data/local-dev/admin/config.ini
```

Before login or run, edit only copied local configs under `data/local-dev/`.

PES local config values:

```ini
debug = True
session = data/local-dev/pes/bot.session
user_session = data/local-dev/pes/user.session
table_cache_fp = data/local-dev/pes/table_cache.json
logo = data/local-dev/pes/logo.png
```

Admin local config values:

```ini
debug = True
session = data/local-dev/admin/bot.session
cache_fp = data/local-dev/admin/repost_cache.pkl
```

Use test chats/channels when possible.

## Login

Only after explicit user approval:

```bash
SENTRY_ENVIRONMENT=development nix develop --command uv run vtraty-pes-bot --config data/local-dev/pes/config.ini --login
SENTRY_ENVIRONMENT=development nix develop --command uv run vtraty-pes-bot --config data/local-dev/pes/config.ini --user-login
SENTRY_ENVIRONMENT=development nix develop --command uv run vtraty-admin-bot --config data/local-dev/admin/config.ini --login
```

## Run

Run one bot in the foreground:

```bash
SENTRY_ENVIRONMENT=development nix develop --command uv run vtraty-pes-bot --config data/local-dev/pes/config.ini
SENTRY_ENVIRONMENT=development nix develop --command uv run vtraty-admin-bot --config data/local-dev/admin/config.ini
```

## Teardown

```bash
# Stop foreground runs with Ctrl-C. If a run was backgrounded, kill only the recorded PID.
test ! -f data/local-dev/pes.pid || kill "$(cat data/local-dev/pes.pid)"
test ! -f data/local-dev/admin.pid || kill "$(cat data/local-dev/admin.pid)"
rm -f data/local-dev/*.pid
rm -rf data/local-dev
```

Never delete user-provided `pes/config.ini`, `admin/config.ini`, session files, or `.env` as part of teardown. Only remove `data/local-dev/` state created by this flow.
