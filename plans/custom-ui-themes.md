# Custom UI Themes

## Context

LEAP2 ships a default theme (`leap/ui/shared/theme.css`) with the "Laboratory Editorial" aesthetic — warm academic tones, DM Sans / Fraunces typography, and a fixed set of CSS custom properties. All built-in pages (landing, 404, readme, logs, students, functions) consume this theme via `/static/` served from `leap/ui/shared/`.

Instructors and lab authors currently have no way to customize the look and feel of LEAP's built-in dashboard pages. Supporting custom themes would let labs match institutional branding or experiment-specific visual identities without forking the LEAP codebase.

## Design

### Folder structure

A lab can ship multiple named themes inside `ui/`. Each subfolder is a theme. Files in a theme folder override the corresponding files in LEAP's built-in `leap/ui/shared/` directory by filename. Any file not overridden falls back to the default.

```
my-lab/
├── README.md
├── ui/
│   ├── dark/                  # ← theme "dark"
│   │   ├── theme.css
│   │   └── logo.svg
│   ├── school-brand/          # ← theme "school-brand"
│   │   ├── theme.css
│   │   ├── navbar.js
│   │   ├── footer.js
│   │   └── logo.png
│   └── minimal/               # ← theme "minimal"
│       └── theme.css
├── experiment-1/
│   └── ...
└── experiment-2/
    └── ...
```

### Theme selection at server start

The theme is selected via a `--theme` flag on `leap run`:

```
leap run --theme dark
leap run --theme school-brand
```

- If `--theme` is not provided, the built-in default theme is used (`leap/ui/shared/`).
- If the specified theme folder doesn't exist under `ui/`, LEAP prints an error listing available themes and exits.
- The lab's README frontmatter can also declare a default theme so that bare `leap run` uses it without requiring the flag:

```yaml
---
name: my-lab
theme: dark
---
```

**Resolution order for theme selection:**

1. `--theme` CLI flag (highest priority)
2. `theme:` field in lab root README frontmatter
3. Built-in default (no custom theme)

### Resolution order for static files

When serving a static asset from `/static/<filename>`:

1. **Selected theme folder** (`<lab_root>/ui/<theme_name>/<filename>`) — highest priority
2. **Package default** (`leap/ui/shared/<filename>`) — fallback

### What can be customized

- **`theme.css`** — CSS custom properties (colors, fonts, radii, shadows), additional styles
- **`navbar.js`** / **`footer.js`** — custom navigation and footer components
- **`landing/index.html`** — custom landing page
- **`404.html`** — custom error page
- **Any additional assets** — images, fonts, extra JS/CSS files referenced by custom overrides

### Theme CSS contract

Custom `theme.css` files should define the same CSS custom properties as the default theme to ensure built-in pages render correctly:

```css
:root {
  --color-bg: ...;
  --color-surface: ...;
  --color-primary: ...;
  --color-primary-hover: ...;
  --color-primary-light: ...;
  --color-primary-rgb: ...;
  --color-accent: ...;
  --color-accent-light: ...;
  --color-text: ...;
  --color-text-muted: ...;
  --color-border: ...;
  --color-success: ...;
  --color-error: ...;
  --color-error-bg: ...;
  --color-warning: ...;
  --color-warning-bg: ...;
  --radius: ...;
  --radius-lg: ...;
  --shadow-sm: ...;
  --shadow: ...;
  --shadow-md: ...;
  --shadow-lg: ...;
  --font-sans: ...;
  --font-display: ...;
}
```

Omitting a variable is safe — the browser will use the property's fallback or inherited value — but may produce visual inconsistencies.

## Changes

### 1. `leap/cli.py` — `--theme` option on `leap run`

Add a `--theme` parameter to the `run` command:

```python
@app.command()
def run(
    host: str = typer.Option("0.0.0.0", help="Bind address"),
    port: int = typer.Option(9000, help="Port"),
    root: Optional[Path] = typer.Option(None, help="Project root override"),
    theme: Optional[str] = typer.Option(None, help="UI theme name (subfolder of ui/)"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable request access logs"),
):
```

If `--theme` is provided, validate that `<root>/ui/<theme>/` exists. If not, list available themes and exit with an error. Pass the resolved theme path to `create_app()`.

If `--theme` is not provided, check the lab README frontmatter for a `theme:` field as fallback.

### 2. `leap/main.py` — accept and apply theme

**`create_app(root=None, theme=None)`**: Accept an optional `theme` parameter.

At startup, resolve the theme directory:

```python
theme_dir = None
if theme:
    candidate = resolved_root / "ui" / theme
    if candidate.is_dir():
        theme_dir = candidate
        logger.info("Using custom theme '%s' from %s", theme, theme_dir)

app.state.theme_name = theme or "default"
app.state.theme_dir = theme_dir
```

**Layered static file mounting**: Create a `MergedStaticFiles` class (or use Starlette's fallback mechanism) that checks the theme directory first, then falls back to `leap/ui/shared/`:

```python
class MergedStaticFiles:
    """Serve static files from multiple directories with priority ordering."""

    def __init__(self, directories: list[Path]):
        self.directories = [d for d in directories if d and d.is_dir()]

    async def __call__(self, scope, receive, send):
        path = scope["path"].lstrip("/")
        for directory in self.directories:
            candidate = directory / path
            if candidate.is_file():
                # delegate to StaticFiles for this directory
                ...
```

Mount order: `[theme_dir, pkg_shared]` — theme files win, missing files fall back to defaults.

### 3. `leap/main.py` — theme-aware landing/404 resolution

Update the existing landing page and 404 fallback logic to check the theme directory first:

```python
for ui_root in [theme_dir, app.state.ui_root, app.state.pkg_ui_root]:
    if ui_root is None:
        continue
    landing_file = ui_root / "landing" / "index.html"
    ...
```

### 4. `leap/core/experiment.py` / `leap/main.py` — `theme` frontmatter field

Add `"theme": ""` to `_LAB_FIELDS` so the lab README can declare a default theme. This is read at startup and used as fallback when `--theme` is not passed on the CLI.

### 5. `leap/cli.py` — list available themes

Add a helper or integrate into `leap doctor` / startup logging to list discovered themes:

```python
def _list_themes(root: Path) -> list[str]:
    ui_dir = root / "ui"
    if not ui_dir.is_dir():
        return []
    return [d.name for d in ui_dir.iterdir() if d.is_dir()]
```

On startup with a custom theme, log the active theme in the rich metadata box alongside lab name, experiments, etc.

### 6. Documentation

Add a section to the lab authoring guide explaining:
- How to create named theme folders under `ui/`
- Selecting a theme via `--theme` or the `theme:` frontmatter field
- Which files can be overridden
- The CSS custom property contract
- Examples of common customizations (school colors, dark mode, logo swap)

## Files

- `leap/cli.py` — `--theme` option on `run`, theme validation, `_list_themes` helper
- `leap/main.py` — `create_app` accepts theme, `MergedStaticFiles`, theme-aware fallback
- `leap/core/experiment.py` — `theme` in lab frontmatter fields (if needed)

## Theme Discovery & Installation

### Registry integration

Themes are published to and discovered from the same leaplive registry (`leaplive/registry`) used for labs and experiments. Theme entries in `registry.yaml` use `type: theme`:

```yaml
- name: midnight
  display_name: Midnight Dark Theme
  description: Dark mode theme with blue accents for low-light classrooms
  type: theme
  author: someone
  url: https://github.com/someone/leap-theme-midnight
  tags:
    - dark
    - accessibility

- name: university-red
  display_name: University Red
  description: Branded theme with crimson tones and serif typography
  type: theme
  author: faculty
  url: https://github.com/faculty/leap-theme-red
  tags:
    - branding
    - serif
```

### Theme repository structure

A theme repository is a simple git repo containing the theme files at its root (no nested `ui/` subfolder needed — the installer handles placement):

```
leap-theme-midnight/
├── README.md          # frontmatter with name, description, tags, type: theme
├── theme.css          # required — the CSS overrides
├── navbar.js          # optional
├── footer.js          # optional
├── logo.svg           # optional
└── preview.png        # optional — screenshot shown in discover output
```

### `leap discover --type theme`

Filter the registry to show only themes. Uses the existing `discover` command with the `--type` filter, which already supports filtering by `entry_type`:

```
$ leap discover --type theme

LEAP Registry  (3 themes)

┌─ midnight ──────────────────────────────────────────────┐
│ Dark mode theme with blue accents for low-light rooms   │
│ author: someone  tags: dark, accessibility              │
│ https://github.com/someone/leap-theme-midnight          │
└─────────────────────────────────────────────────────────┘
```

No new CLI command needed — `discover` already supports `--type`.

### `leap install --theme <name-or-url>`

Install a theme from the registry or a direct URL into the lab's `ui/<theme_name>/` folder:

```
$ leap install --theme midnight
# → Fetching theme 'midnight' from registry...
# → Cloning https://github.com/someone/leap-theme-midnight
# → Installed to ui/midnight/
# → Run with: leap run --theme midnight

$ leap install --theme https://github.com/faculty/leap-theme-red
# → Cloning https://github.com/faculty/leap-theme-red
# → Installed to ui/university-red/
# → Run with: leap run --theme university-red
```

**Registry lookup**: When `--theme` is passed and the argument is not a URL, search `registry.yaml` for an entry with `type: theme` matching the name, then use its `url`.

**Direct URL**: If the argument looks like a URL, clone directly without registry lookup.

**Installation steps**:

1. Clone the theme repo to a temp directory
2. Read the theme's `README.md` frontmatter for the `name` field
3. Copy theme files (excluding `.git/`, `README.md`, `preview.png`) into `<lab_root>/ui/<name>/`
4. Log the installed theme name and suggest the `--theme` flag

### `leap publish` for themes

The existing `publish` command already reads frontmatter and submits to the registry. Themes just need `type: theme` in their README frontmatter — the publish flow handles the rest identically to labs and experiments.

### Changes for discovery & installation

#### `leap/cli.py` — `install` command

Add a `--theme` flag to the existing `install` command (or create `install_theme_fn`):

```python
@app.command("install")
def install(
    source: str = typer.Argument(..., help="Experiment name, URL, or theme name"),
    name: Optional[str] = typer.Option(None, help="Override installed name"),
    root: Optional[Path] = typer.Option(None, help="Project root override"),
    theme: bool = typer.Option(False, "--theme", help="Install as a UI theme"),
):
```

When `--theme` is set:

```python
def install_theme_fn(source: str, root: Path):
    # If source is not a URL, look it up in registry
    if not source.startswith(("http://", "https://")):
        entries = discover_registry_fn(entry_type="theme")
        match = next((e for e in entries if e["name"] == source), None)
        if not match:
            raise typer.BadParameter(f"Theme '{source}' not found in registry")
        source = match["url"]

    # Clone to temp, read name from frontmatter, copy to ui/<name>/
    ...
```

#### `registry.yaml` — schema extension

Add `type: theme` as a valid entry type alongside `experiment` and `lab`. No schema change needed in the registry itself — just a new conventional value.

#### `leap/cli.py` — `discover` command

Already supports `--type` filtering. Themes appear when `--type theme` is passed. Optionally enhance the display to show a "Themes" section header when themes are present in unfiltered results.

### Files (discovery & installation)

- `leap/cli.py` — `--theme` flag on `install`, `install_theme_fn`, theme-aware display in `discover`

## Verification

1. **No custom theme**: `leap run` with no `ui/` folder and no `--theme` → default theme as before
2. **CLI theme selection**: `leap run --theme dark` with `ui/dark/theme.css` → dark theme applied
3. **Frontmatter default**: Lab README has `theme: dark`, bare `leap run` → dark theme applied
4. **CLI overrides frontmatter**: README says `theme: dark`, `leap run --theme minimal` → minimal wins
5. **Invalid theme**: `leap run --theme nonexistent` → error listing available themes, server does not start
6. **CSS-only override**: Theme folder with only `theme.css` → custom colors, all other assets fall back to defaults
7. **Full override**: Theme folder with `theme.css`, `navbar.js`, `footer.js` → all three customized
8. **Additional assets**: Theme folder with `logo.svg` → accessible at `/static/logo.svg`
9. **No themes exist**: `leap run --theme foo` with no `ui/` folder → clear error message
10. **Hot reload**: Changing a file in the active theme reflects on next page load (no server restart needed)
11. **Discover themes**: `leap discover --type theme` → shows only theme entries from registry
12. **Install from registry**: `leap install --theme midnight` → clones and installs to `ui/midnight/`
13. **Install from URL**: `leap install --theme https://github.com/...` → clones and installs correctly
14. **Publish theme**: `leap publish` from a theme repo with `type: theme` in frontmatter → submits to registry
15. **Install + run**: `leap install --theme midnight && leap run --theme midnight` → full end-to-end flow
