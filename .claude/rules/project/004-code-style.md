# Code Style And Packaging
<!-- .claude/rules/project/004-code-style.md -- dependencies, uv2nix, containers, comments -->

## Python Packaging

- Use uv workspace metadata and the single root `uv.lock`.
- Do not add Poetry, per-package locks, dream2nix, or generated `lock.<system>.json` files.
- Keep shared runtime dependencies in `common/` only when `common/src` imports them.
- Bot packages must still declare libraries they import directly; do not fake inherited runtime dependencies through docs.
- Preserve the flattened `*/src` package roots and setuptools `package-dir` mappings.

## Nix Packaging

- Use uv2nix with `pyproject.nix` and `build-system-pkgs`.
- Keep `nixpkgs` as the single root flake input; uv2nix, pyproject.nix, and build-system-pkgs must follow it.
- Do not introduce secondary nixpkgs locks.
- Keep flake outputs greppable: packages are `.#pes`, `.#admin`, `.#pes-image`, `.#admin-image`; apps are `.#pes`, `.#admin`.
- Shared Nix helper code belongs in `nix/shared/default.nix`; bot-specific output names stay visible in `flake.nix`.

## Containers And Runtime Tools

- Container images must stay deterministic, pure, and non-root.
- Do not read `GIT_SHA` or ambient env vars during Nix evaluation.
- `ffmpeg` for PES runtime comes from `imageio-ffmpeg`; do not reintroduce host or image-level `ffmpeg` unless there is a measured need.
- `wkhtmltoimage` is provided by the PES image for `imgkit` table rendering. Keep fontconfig paths explicit in the image environment when changing this area.
- Do not reintroduce Docker Compose for local dev; use `.claude/rules/operations/100-local-dev.md`.

## Comments

- Preserve useful comments when moving code.
- Add new comments only for non-obvious intent or constraints.
- Use `@TODO:`, `@NOTE:`, and `@WARNING:` prefixes for durable comments.
- Delete tombstone comments instead of leaving notes about removed code.
