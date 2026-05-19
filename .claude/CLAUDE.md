# CLAUDE.md

## Project Overview

Telegram bot for tracking military equipment losses from OSINT channels. Reads posts from a source channel (via a user account), parses equipment names/status with an LLM, caches results, and generates daily/weekly HTML table images posted to a target channel (via a bot account).

## Build & Run

```bash
# Poetry (local dev)
poetry install
python -m vtraty_pes_bot --config config.ini

# Nix dev shell (provides ruff, mypy, actionlint, zizmor, poetry, wkhtmltopdf, ffmpeg)
nix develop

# Nix build & run
nix run .#vtraty-pes-bot.lock        # regenerate lock.x86_64-linux.json after dep changes
nix build .#vtraty-pes-bot

# Docker (Nix-built image)
nix build .#docker-image && docker load < ./result
docker-compose up -d
```

### CLI flags
- `--config <path>` (required): path to config.ini
- `--login`: authenticate the bot account (requires `[telegram] token`)
- `--user-login`: authenticate the user account interactively

## Code Formatting & Verification

Line length: **131**. CI enforces this.

**Always run all of these before declaring changes ready:**

```bash
# Metadata
poetry check

# Python
ruff format --check src
ruff check src
mypy src

# GitHub Actions
actionlint
zizmor .github/workflows

# Nix
nix shell nixpkgs#alejandra -c alejandra -c .
# --no-lambda-arg: default.nix's cleanSourceWith filter requires a `type` arg by signature that we don't use.
nix shell nixpkgs#deadnix -c deadnix --no-lambda-arg flake.nix default.nix
nix flake check --all-systems
nix build .#vtraty-pes-bot
nix build .#docker-image
```

## Preferences

- **No local memory files.** Store persistent context in this CLAUDE.md (git-tracked, machine-portable), not in `.claude/projects/*/memory/`.
- **Flake pattern:** `forEachSystem` from `kittyandrew/pail` — no flake-utils. Reference: `~/dev/kittyandrew/pail/flake.nix`.

## File Index

> **Maintenance rule:** when adding, removing, or renaming files, update this index in the same commit. Future sessions rely on it to navigate the codebase.

### Source — `src/vtraty_pes_bot/`

| File | Purpose |
|------|---------|
| `__main__.py` | `python -m` entry point — calls `main_cli()` |
| `__init__.py` | CLI arg parsing (`main_cli`), async main loop (`main`). Creates two `TGSpawner` instances (bot + user), calls `tinit()` to load all tmodules, then runs the event loop forever. |
| `new_account.py` | `TGSpawner` class — Telegram session loading (`load_account`) and interactive login (`login`). Account creation was removed (see git history). |
| `llm.py` | `parse_messages()` — sends batched message texts to OpenAI (`gpt-5-mini`) with structured output. Returns `list[Item]`. Pydantic models: `Item` (name, ownership, status, post_date), `Vehicles` (list wrapper). |
| `prompts.py` | LLM prompt templates: `VEHICLE_EXPORT_SYSTEM` (few-shot examples for equipment extraction), `VEHICLE_EXPORT_EXTRA` (date context + gsheet reference data), `VEHICLE_EXPORT_USER` (message wrapper). |
| `gsheets.py` | Google Sheets API integration. `get_gsheet_prompt()` fetches vehicle reference lists (columns A:B) and keywords (D:E) for LLM context. `get_vehicle_types()` fetches type enum values (column C) and builds a `CustomEnum` for table categorization. |
| `template.py` | Jinja2 HTML template string for the losses table. `render_table()` in table.py uses imgkit to convert it to a JPEG image. |

### Telegram Modules — `src/vtraty_pes_bot/tmodules/`

Dynamically loaded at startup: `__init__.py` imports every `.py` file in the directory and calls its `init(**context)` coroutine. To add a new module, create a file with an `async def init(...)` — it will be auto-discovered.

| File | Purpose |
|------|---------|
| `__init__.py` | Module loader — dynamic import + `init()` dispatch. |
| `table.py` | **Core module.** Daily scheduled table generation, `/table` command, inline keyboard regeneration callbacks. Key functions: `generate_table()`, `generate_cache_for_date()`, `scheduled_table()` (background task), `convert_counter_into_lines()`. Uses a 6am-to-6am day boundary. Weekly summary generated on Mondays via `asyncio.gather` over 7 days. Callback button data format: `v0\|channel_id\|date\|msg_id1,msg_id2`. |
| `downloader.py` | Multi-platform shortform video downloader (Instagram, Facebook, YouTube Shorts, TikTok, X/Twitter, FunnyJunk). Uses yt-dlp. Validates URLs against `MATCH_RULES`, downloads video + thumbnail, posts with attribution. FunnyJunk uses `match_subdomains` for CDN domains and `ffprobe` fallback for video metadata. |
| `gatekeep.py` | New-user gatekeeper for a target chat. Sends join message, estimates account age via polynomial interpolation on historical Telegram ID data ("guesstimator"), reports user info to owner, auto-kicks after 10min if user doesn't post. |
| `watermark.py` | `/watermark` command — overlays a bouncing logo on videos (OpenCV frame-by-frame + ffmpeg for audio) or a centered logo on images. Restricted to configured user IDs. |

### Build & Packaging

| File | Purpose |
|------|---------|
| `pyproject.toml` | Package metadata, dependencies, entry point (`vtraty-pes-bot = vtraty_pes_bot:main_cli`). Poetry build backend. |
| `flake.nix` | Nix flake — defines `vtraty-pes-bot` package (via dream2nix), `docker-image` (dockerTools), and dev shell. System deps in image: wkhtmltopdf, ffmpeg. |
| `default.nix` | dream2nix module config — pip backend, build overrides for packages needing setuptools/flit-core. |
| `lock.x86_64-linux.json` | dream2nix pip lock file. Regenerate with `nix run .#vtraty-pes-bot.lock` after changing dependencies. |
| `poetry.lock` | Poetry lock file. |
| `docker-compose.yml` | Runs the Nix-built image with `./data` volume mount and `.env` file. |

### CI/CD — `.github/workflows/`

| File | Trigger | What it does |
|------|---------|-------------|
| `ci.yml` | Push to master, PRs | actionlint, zizmor, Poetry metadata, Nix format/lint, Ruff format/lint, mypy |
| `build.yml` | Push to master | Token-free quality gates, then Attic cache setup, `nix flake check --all-systems`, package build, Docker image build |

### Config & Data

| File | Purpose |
|------|---------|
| `config.ini.sample` | Annotated config template. Sections: `[general]` (owner, sessions, timezone, table settings, gsheet credentials), `[telegram]` (api_id, api_hash, optional token), `[repost]` (source channel), `[guesstimator]` (historical data path). |
| `.env.sample` | `CONFIG_PATH=config.ini` — used by docker-compose. Actual `.env` also needs `OPENAI_API_KEY`, optionally `LANGCHAIN_API_KEY`, and optionally `SENTRY_DSN` + `SENTRY_ENVIRONMENT`. |
| `data/` | Runtime data directory (gitignored). Contains session files, config, table cache JSON, logo, font, mutelist. |

### Docs — `docs/`

| File | Purpose |
|------|---------|
| `observability.md` | Sentry integration details: what's traced, how to add breadcrumbs, environment/release tracking. |

## Architecture

### Two-Account Model

The bot uses **two Telegram accounts** simultaneously:
- **Bot account** (`client`): responds to commands, posts tables, handles callbacks, sends messages.
- **User account** (`user`): reads messages from source channels (bots can't read channel history). Used by `iter_messages()` in table.py.

Both are created via `TGSpawner` in `__init__.py` and passed through the shared `context`/`storage` dict.

### Context/Storage Pattern

`__init__.py` creates a `context` dict containing `logger`, `config`, `client`, `user`, and a self-reference `storage`. This dict is passed to every tmodule's `init()` via `**context`. Modules destructure what they need from kwargs.

### Table Generation Pipeline

1. `scheduled_table()` sleeps until `table_schedule_at` time, then calls `generate_table()`
2. `generate_table()` fetches messages from source channel via user account (`iter_messages` with `offset_date`)
3. Messages filtered by `is_relevant_post()` (must have text, not an archive/undetermined post)
4. Batched into groups of 3, sent to `parse_messages()` in parallel (semaphore=8)
5. `parse_messages()` calls OpenAI with structured output → `list[Item]`
6. Results cached in JSON file keyed by date string (`dd.mm.YYYY`)
7. Items aggregated into counters by vehicle name, categorized by type enum from Google Sheets
8. `convert_counter_into_lines()` formats counters into table rows
9. HTML rendered via Jinja2 template, converted to JPEG via imgkit (wkhtmltoimage)
10. On Mondays: weekly summary generated in parallel (`asyncio.gather` over 7 days of cache)

### Day Boundary

The "day" runs **6:00 AM to 6:00 AM** in the configured timezone (typically `Europe/Kyiv`). `get_time_range()` returns (start, end) aligned to this boundary with DST normalization via `pytz`.

## Key Dependencies

- **telethon** + **telethon-tgcrypto**: Telegram MTProto client
- **langchain-openai**: LLM structured output (ChatOpenAI)
- **yt-dlp** + **bgutil-ytdlp-pot-provider**: video downloading
- **opencv-python** + **ffmpeg**: video/image watermarking
- **imgkit**: HTML→image (requires wkhtmltopdf binary)
- **pytz**: timezone handling (not stdlib zoneinfo — uses `normalize()` for DST)
- **aiocache**: TTL caching for callback rate limiting and channel admin lookups
- **sentry-sdk**: Error reporting, traces, and breadcrumbs (init in `__init__.py`)
- **aiohttp**: HTTP client for Google Sheets API, thumbnail downloads

## Observability

This project reports errors, traces, and breadcrumbs to Sentry via `SENTRY_DSN`. See `docs/observability.md` for full details.

**Conventions:**
- `sentry_sdk.init()` lives in `__init__.py:_main()` — runs once at startup
- Background tasks and tmodule handlers are covered by Sentry's default exception capture
- Critical decision points (LLM calls, table generation, video downloads) log breadcrumbs via `sentry_sdk.add_breadcrumb()`
- Errors propagate to Sentry — don't silently catch and discard exceptions
- New features should add breadcrumbs on their key code paths

**Environment:** `SENTRY_DSN` must be set. Unset or omit it to disable reporting entirely (SDK becomes a no-op).

<!-- sentry-verified -->

## No Test Suite

There is currently no test suite. Verify changes by reading the code and checking `nix build` / formatting.
