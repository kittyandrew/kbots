# kbots

uv/Nix monorepo for Vtraty Telegram bots.

Packages:

- `pes/` — equipment-loss table bot, shortform downloader, gatekeeper, and watermark command.
- `admin/` — admin/repost bot with purge and source-to-target mirroring modules.
- `common/` — shared Telegram runtime, login/session handling, Sentry setup, and tmodule loading.

## Setup

```bash
cp pes/config.ini.sample pes/config.ini
cp admin/config.ini.sample admin/config.ini
```

### uv

```bash
nix develop --command uv sync --frozen --all-packages
nix develop --command uv run vtraty-pes-bot --config pes/config.ini
nix develop --command uv run vtraty-admin-bot --config admin/config.ini
```

### Nix

```bash
nix develop

nix build .#pes
nix build .#admin
nix run .#pes -- --help
nix run .#admin -- --help
nix build .#pes-image && docker load < ./result
nix build .#admin-image && docker load < ./result
```

## Configuration

See [`pes/config.ini.sample`](../pes/config.ini.sample) and [`admin/config.ini.sample`](../admin/config.ini.sample) for all options.

PES requires Telegram API credentials, a bot session, a user session, OpenAI API key, and Google Sheets API key. Admin requires Telegram API credentials and source/target channel IDs.
