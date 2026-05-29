# kbots

uv/Nix monorepo for Vtraty Telegram bots.

## Packages

| Package | CLI | Purpose |
|---------|-----|---------|
| `pes/` | `vtraty-pes-bot` | Equipment-loss table bot, shortform downloader, gatekeeper, and watermark command. |
| `admin/` | `vtraty-admin-bot` | Admin/repost bot with purge and source-to-target mirroring modules. |
| `common/` | none | Shared Telegram runtime, login/session handling, Sentry setup, and tmodule loading. |

## Setup

```bash
cp pes/config.ini.sample pes/config.ini
cp admin/config.ini.sample admin/config.ini
nix develop --command uv sync --frozen --all-packages
```

Edit copied configs before running. See `pes/config.ini.sample` and `admin/config.ini.sample` for all options.

## Run

```bash
nix develop --command uv run vtraty-pes-bot --config pes/config.ini
nix develop --command uv run vtraty-admin-bot --config admin/config.ini
```

CLI smoke checks:

```bash
nix run .#pes -- --help
nix run .#admin -- --help
```

## Build

```bash
nix build .#pes
nix build .#admin
nix build .#pes-image
nix build .#admin-image
```

## Check

```bash
nix develop --command uv lock --check
nix develop --command ruff format --check admin common pes
nix develop --command ruff check admin common pes
nix develop --command uv run mypy admin/src common/src pes/src
nix flake check --all-systems
```

## Configuration

PES requires Telegram API credentials, a bot session, a user session, OpenAI API key, and Google Sheets API key.

Admin requires Telegram API credentials and source/target channel IDs.

Optional Sentry reporting is controlled by `SENTRY_DSN`, `SENTRY_ENVIRONMENT`, and `SENTRY_RELEASE`; see `docs/observability.md`.
