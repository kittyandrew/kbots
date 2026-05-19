# vtraty-pes-bot

Telegram bot for a military equipment losses tracking community. Core feature is automated daily and weekly summary tables — the bot reads an OSINT source channel covering the Russia-Ukraine war, uses an LLM to extract equipment names, types, ownership, and status from posts, then renders categorized loss tables as images and posts them to the group.

Also serves as a general-purpose group utility bot:
- **Video downloader** — reposts shortform videos from Instagram, TikTok, YouTube Shorts, Facebook, and X/Twitter with attribution
- **Watermarking** — overlays a bouncing logo on videos or a centered logo on images
- **Gatekeeping** — greets new members, estimates account age, auto-kicks if they don't post within 10 minutes

## Setup

```bash
cp config.ini.sample config.ini  # Edit with your credentials
```

### Poetry (local dev)

```bash
poetry install
python -m vtraty_pes_bot --config config.ini
```

### Nix

```bash
# Dev shell (provides ruff, mypy, actionlint, zizmor, poetry, wkhtmltopdf, ffmpeg)
nix develop

# Build
nix build .#vtraty-pes-bot

# Regenerate lock after dependency changes
nix run .#vtraty-pes-bot.lock
```

### Docker (Nix-built image)

```bash
nix build .#docker-image && docker load < ./result
docker-compose up -d
```

## Configuration

See [`config.ini.sample`](../config.ini.sample) for all options. Requires:
- Telegram API credentials (`api_id`, `api_hash`)
- A bot account session and a user account session (user account reads channel history that bots can't access)
- OpenAI API key (via `.env`)
- Google Sheets API key (for vehicle reference data and type categorization)
