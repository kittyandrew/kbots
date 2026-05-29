# Rules Structure
<!-- .claude/rules/000-rules-meta.md -- rule conventions, precedence, naming, sizing -->

This project inherits the global CLAUDE constitution and narrows it with project-specific rules. When instructions conflict, narrower scope wins: current user request, then project rules, then global constitution, then general defaults.

## Naming Convention

Use `.claude/rules/<section>/NNN-name.md`, where `NNN` is globally unique inside this repo.

| Prefix | Section | Contents |
|--------|---------|----------|
| `0xx` | `project/` | Architecture, file map, build, code style, observability |
| `1xx` | `operations/` | Local operational procedures, including dev bot runs |

Add new namespace prefixes here before creating files in that range. Each rule file starts with an H1 and an HTML comment: `<!-- path -- keywords -->`.

## What Belongs In Rules

Rules are short directives agents need during relevant work. Detailed rationale and long references belong in `docs/`; rules should point to files and docs rather than copy them.

Keep `.claude/CLAUDE.md` as a compact router. Do not move detailed guidance back into it.

## Constraints

- Keep each rule under 100 lines unless there is a strong reason to split differently.
- Prefer file pointers over inline explanations when code is authoritative.
- When adding, removing, or renaming files, update `.claude/rules/project/002-file-map.md` in the same staged change.
- Do not copy the global Part I-IV CLAUDE constitution into this repo; inherit it and add kbots-specific deltas here.
