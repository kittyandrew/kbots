# Architecture
<!-- .claude/rules/project/001-architecture.md -- package layout, runtime, tmodules, bot models -->

`kbots` has three top-level Python packages:

| Package | Import | CLI | Purpose |
|---------|--------|-----|---------|
| `common/` | `kbots_common` | none | Shared Telegram runtime, login/session handling, Sentry setup, tmodule loading. |
| `pes/` | `vtraty_pes_bot` | `vtraty-pes-bot` | Equipment-loss table bot, shortform downloader, gatekeeper, watermark command. |
| `admin/` | `vtraty_admin_bot` | `vtraty-admin-bot` | Admin/repost bot with purge and source-to-target mirroring modules. |

Keep the flattened layout: `admin/src`, `common/src`, and `pes/src` are package roots via setuptools `package-dir`. Do not introduce a `packages/` layer or nested `src/<package_name>/` directories.

## Shared Runtime

Both bots enter through package-specific `main_cli()` wrappers and then call `kbots_common.main_cli()` / `run_bot()`.

The shared runner reads config, initializes logging and Sentry, loads Telegram sessions, builds a mutable `context`, calls the bot's tmodule initializer, and keeps the event loop running.

The context always includes `logger`, `config`, and `storage`. It includes `client` after bot session load. PES also includes `user` because it needs a user account for reading channel history.

## Module Loading

Each bot has a `tmodules` package. The package `__init__.py` calls the common loader, which imports every alphabetic `.py` file in the directory and awaits `init(**context)` if present.

To add a module, create a new file with `async def init(...)`. Keep bot-specific behavior in the bot package; only move code to `common/src` when both bots actually use it or it is part of the shared runtime.

## PES Two-Account Model

| Account | Context key | Purpose |
|---------|-------------|---------|
| Bot account | `client` | Commands, callbacks, table posts, downloader replies, gatekeeper messages, watermark replies. |
| User account | `user` | Reads source channel history because bot accounts cannot reliably read channel history. |

## Admin Single-Account Model

Admin uses one Telegram session as `client`. It handles outgoing `/purge` commands in the configured admin chat and mirrors source-channel events to the target channel.

## Table Generation Pipeline

1. `scheduled_table()` sleeps until `table_schedule_at`, then calls `generate_table()`.
2. `generate_table()` reads source-channel messages through the PES `user` account using the configured 6am-to-6am window.
3. `is_relevant_post()` filters archive and undetermined-affiliation posts.
4. Messages are batched in groups of 3 and sent to `parse_messages()` with semaphore-bounded concurrency.
5. Results are cached in JSON by `dd.mm.YYYY` date string.
6. Items are aggregated by vehicle/status, categorized via Google Sheets vehicle types, rendered to HTML, and converted to JPEG through `imgkit`/`wkhtmltoimage`.
7. Mondays generate a weekly summary with `asyncio.gather()` over 7 days of cache generation.
