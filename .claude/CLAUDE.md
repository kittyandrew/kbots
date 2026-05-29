# kbots

uv/Nix monorepo for Vtraty Telegram bots. `pes/`, `admin/`, and `common/` are top-level uv workspace packages with flattened `*/src` package roots.

Project rules live in `.claude/rules/` and are auto-loaded. `.claude/rules/000-rules-meta.md` defines the rule structure and precedence.

Do not duplicate global CLAUDE constitution text here. This project inherits global instructions and narrows them with `.claude/rules/`.
