# Build And Verification
<!-- .claude/rules/project/003-build-verification.md -- uv, nix, CI, checks -->

Python target is 3.13. Line length is 131. Run commands from the repo root.

## Build And Run

```bash
# Sync workspace.
nix develop --command uv sync --frozen --all-packages

# CLI smoke runs.
nix develop --command uv run vtraty-pes-bot --help
nix develop --command uv run vtraty-admin-bot --help

# Local config runs.
nix develop --command uv run vtraty-pes-bot --config pes/config.ini
nix develop --command uv run vtraty-admin-bot --config admin/config.ini

# Nix packages and apps.
nix build .#pes
nix build .#admin
nix run .#pes -- --help
nix run .#admin -- --help

# Nix-built container images.
nix build .#pes-image
nix build .#admin-image
```

## Checks

Always run the relevant subset before declaring work ready:

```bash
# Workspace metadata and lock.
nix develop --command uv lock --check
nix develop --command uv sync --frozen --all-packages

# Python.
nix develop --command ruff format --check admin common pes
nix develop --command ruff check admin common pes
nix develop --command uv run mypy admin/src common/src pes/src

# Nix.
nix develop --command alejandra -c .
nix develop --command deadnix flake.nix nix/shared/default.nix
nix flake show --all-systems --option substitute false
nix build .#admin .#pes .#admin-image .#pes-image --no-link --option substitute false
nix flake check --all-systems --option substitute false

# CLI smoke tests.
nix run .#pes -- --help
nix run .#admin -- --help
```

For GitHub Actions changes, also run:

```bash
nix develop --command actionlint
nix develop --command zizmor .github/workflows
```

## CI Rules

GitHub Actions keep token-free quality gates in a separate job from mainline build/cache work. Keep actions SHA-pinned, `permissions: contents: read`, and `persist-credentials: false` on checkout. The Attic token must stay in a `main`-push-only job that depends on quality.

No PR trigger unless explicitly requested.
