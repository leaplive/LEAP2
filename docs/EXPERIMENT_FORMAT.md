# Experiment README Format

Each experiment has a `README.md` with YAML frontmatter:

```markdown
---
name: default
type: experiment
display_name: Default Lab
description: Basic RPC lab with square, cubic, Rosenbrock.
version: "1.0.0"
entry_point: readme
leap_version: ">=1.0"
require_registration: true
pages:
  - {name: "Scores", file: "scores.html", admin: true}
---

# Instructions

1. Register your student ID.
2. Use the RPC client to call functions.
```

| Field | Default | Description |
|---|---|---|
| `name` | folder name | Identifier (folder name is source of truth for routing) |
| `type` | — | Must be `experiment` |
| `display_name` | folder name | Human-readable name |
| `description` | `""` | Short description |
| `version` | `""` | Experiment version (shown on landing page card) |
| `author` | `""` | Experiment creator |
| `organization` | `""` | Institution or company |
| `repository` | `""` | Git URL (used by `leap publish`) |
| `tags` | `[]` | List of tags (used by `leap discover`) |
| `entry_point` | `readme` | `readme` = experiment README page; or a UI file in `ui/` (e.g. `dashboard.html`) |
| `leap_version` | _(none)_ | Minimum LEAP2 version required (enforced; `>=1.0`, `==1.0.0`, or bare `1.0`) |
| `require_registration` | `true` | Require student registration for RPC |
| `pages` | `[]` | Extra navbar links: `[{name, file, admin}]`. Admin-only pages hidden for non-admins. |

> [!NOTE]
> Experiments always run inside a lab. To use a standalone experiment from GitHub, first create a lab with `leap init`, then install with `leap add <url>`.

> [!WARNING]
> **Experiment names must be lowercase.** Folder names must match `[a-z0-9][a-z0-9_-]*` — only lowercase letters, digits, hyphens, and underscores are allowed (e.g. `monte-carlo`, `gradient-descent-2d`). Folders with uppercase characters are **silently skipped** at discovery. Use `display_name` in frontmatter for human-readable names.
