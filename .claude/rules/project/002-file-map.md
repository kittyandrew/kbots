# File Map
<!-- .claude/rules/project/002-file-map.md -- repository inventory, update when files move -->

When adding, removing, or renaming files, update this map in the same staged change.

## Shared Runtime: `common/src/`

| File | Purpose |
|------|---------|
| `__init__.py` | Public exports for `kbots_common`. |
| `main.py` | Shared CLI parser and bot runner. Sets logging, Sentry, Telegram sessions, module context, and event loop lifecycle. |
| `new_account.py` | `TGSpawner` and `BadAccountError` for Telegram session loading and interactive login. |
| `tmodules.py` | Dynamic tmodule discovery and `init(**context)` dispatch. |

## PES Bot: `pes/src/`

| File | Purpose |
|------|---------|
| `__main__.py` | `python -m vtraty_pes_bot` entry point. |
| `__init__.py` | PES wrapper around `kbots_common.run_bot`; enables bot login by token and user-account login. |
| `llm.py` | `parse_messages()` with OpenAI structured output. Models: `Item` and `Vehicles`. |
| `prompts.py` | LLM prompt templates for equipment extraction. |
| `gsheets.py` | Google Sheets integration for LLM reference data and vehicle type enum values. |
| `template.py` | Jinja2 HTML template string for rendered losses tables. |

## PES Modules: `pes/src/tmodules/`

| File | Purpose |
|------|---------|
| `__init__.py` | Calls `kbots_common.init_modules()` for the PES tmodule package. |
| `table.py` | Daily/weekly equipment-loss table generation, `/table`, regeneration callbacks, and day-boundary logic. |
| `downloader.py` | Shortform downloader using yt-dlp, `imageio-ffmpeg`, and OpenCV metadata fallback. |
| `gatekeep.py` | New-user gatekeeper with delayed kick cancellation and optional Telegram ID age guesstimation. |
| `watermark.py` | `/watermark` command for images/videos using OpenCV and `imageio-ffmpeg`. |

## Admin Bot: `admin/src/`

| File | Purpose |
|------|---------|
| `__main__.py` | `python -m vtraty_admin_bot` entry point. |
| `__init__.py` | Admin wrapper around `kbots_common.run_bot`; single Telegram session only. |

## Admin Modules: `admin/src/tmodules/`

| File | Purpose |
|------|---------|
| `__init__.py` | Calls `kbots_common.init_modules()` for the admin tmodule package. |
| `admin.py` | `/purge` command for kicking inactive/non-speaking users from the configured admin chat. |
| `repost.py` | Mirrors posts, albums, edits, and deletes with TTL cache-backed message ID mapping. |

## Build, CI, Config, Docs

| File | Purpose |
|------|---------|
| `.github/README.md` | Repository overview, setup, run, build, and check commands. |
| `pyproject.toml` | Root uv workspace and shared ruff/mypy config. |
| `uv.lock` | Single uv workspace lock file. Update with uv only. |
| `common/pyproject.toml` | `kbots-common` metadata and setuptools package-dir mapping. |
| `pes/pyproject.toml` | `vtraty-pes-bot` metadata, dependencies, CLI script, package-dir mapping. |
| `admin/pyproject.toml` | `vtraty-admin-bot` metadata, dependencies, CLI script, package-dir mapping. |
| `flake.nix` | Flake inputs/outputs, bot-specific env/image wiring, apps, dev shell. |
| `nix/shared/default.nix` | Shared Nix helpers for uv2nix sets, virtualenvs, images, and `wkhtmltox`. |
| `.github/workflows/build.yml` | Main-only quality gates, Nix check/build, Attic cache push after quality. |
| `pes/config.ini.sample` | PES config template. |
| `admin/config.ini.sample` | Admin config template. |
| `.env.sample` | Optional local Sentry env vars. |
| `docs/observability.md` | Sentry integration and breadcrumb conventions. |

## Agent Instructions

| File | Purpose |
|------|---------|
| `.claude/CLAUDE.md` | Compact project instruction router. Keep detailed rules out of this file. |
| `.claude/rules/000-rules-meta.md` | Rule structure, precedence, naming, and sizing conventions. |
| `.claude/rules/project/001-architecture.md` | Package layout, shared runtime, tmodule loading, and bot models. |
| `.claude/rules/project/002-file-map.md` | This repository inventory. Update when files move. |
| `.claude/rules/project/003-build-verification.md` | uv, Nix, CI, and verification commands. |
| `.claude/rules/project/004-code-style.md` | Python/Nix packaging, containers, and comments. |
| `.claude/rules/project/005-observability.md` | Sentry environment, breadcrumbs, and release rules. |
| `.claude/rules/operations/100-local-dev.md` | Local dev bot setup, login, run, and teardown. |
