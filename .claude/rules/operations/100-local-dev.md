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

For the fact-check module, set `[factcheck]` in the PES config: `chat_ids` to a test chat, a random `opencode_password`, a random 32+ character `callback_token`, `opencode_url = http://127.0.0.1:4096`, and `state_fp = data/local-dev/pes/factcheck_strikes.json`.

## Login

Only after explicit user approval:

```bash
SENTRY_ENVIRONMENT=development nix develop --command uv run vtraty-pes-bot --config data/local-dev/pes/config.ini --login
SENTRY_ENVIRONMENT=development nix develop --command uv run vtraty-pes-bot --config data/local-dev/pes/config.ini --user-login
SENTRY_ENVIRONMENT=development nix develop --command uv run vtraty-admin-bot --config data/local-dev/admin/config.ini --login
```

## Run

The fact-check sidecar runs beside PES, with values matching the PES `[factcheck]` section. It calls the real model on the user's ChatGPT plan, so it needs the same explicit approval as a Telegram run:

```bash
PES_OPENCODE_STATE=data/opencode OPENCODE_SERVER_PASSWORD=<opencode_password> \
  KBOTS_CALLBACK_URL=http://127.0.0.1:8765 KBOTS_CALLBACK_TOKEN=<callback_token> \
  nix run .#pes-opencode -- --hostname 127.0.0.1 --port 4096
```

The first run needs the ChatGPT login (recipe in `docs/factcheck.md`): start it, give the user the URL and code, and poll until `complete`. It persists in `data/opencode`, outside the teardown below, and the evals reuse it. Delete it only to log out.

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
test ! -f data/local-dev/opencode.pid || kill "$(cat data/local-dev/opencode.pid)"
rm -f data/local-dev/*.pid
rm -rf data/local-dev
```

Never delete user-provided `pes/config.ini`, `admin/config.ini`, session files, or `.env` as part of teardown. Only remove `data/local-dev/` state created by this flow.
