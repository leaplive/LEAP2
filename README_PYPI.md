# LEAP2

**Live Experiments for Active Pedagogy** — An interactive platform that turns Python functions into live, multi-student experiments. Drop functions in a folder, and students call them from Python, JavaScript, Julia, C, or C++ while every call is logged for analysis and visualization.

## Quick Start

**Prerequisites:** Python 3.10+

```bash
# Install (pick one)
pip install git+https://github.com/leaplive/LEAP2.git    # from GitHub
pip install -e .                                           # from local clone

# Set up and run
leap init                    # create lab structure, set admin password
leap run                     # start server at http://localhost:9000
```

Open http://localhost:9000 — the landing page lists available experiments.

## How It Works

**1. Write a function:**

```python
# experiments/my-lab/funcs/functions.py
def square(x: float) -> float:
    """Return x squared."""
    return x * x
```

**2. Students call it remotely:**

```python
from leap.client import Client
c = Client("http://localhost:9000", student_id="s001", experiment="my-lab")
c.square(7)  # 49
```

Every call is automatically logged with args, result, timestamp, student ID, and trial name. Logs are queryable via API, CLI, or the built-in web UI.

Clients are available in **Python, JavaScript, Julia, C, and C++** — students use whichever language their course requires.

## Key Features

| Feature | Description |
|---------|-------------|
| **RPC Server** | Python functions auto-exposed as HTTP endpoints |
| **Per-Experiment Isolation** | Each experiment has its own functions, UI, and DuckDB database |
| **Automatic Logging** | Every call logged with args, result, timestamp, student ID, trial |
| **Student Registration** | Per-experiment registration with admin management and bulk CSV import |
| **Rate Limiting** | Per-function, per-student; default 120/min; configurable via `@ratelimit` |
| **Decorators** | `@nolog`, `@noregcheck`, `@ratelimit`, `@adminonly`, `@withctx` |
| **Multi-Language Clients** | Python, JavaScript, Julia, C, C++ |
| **Decoupled Visualizations** | Log Client abstraction for building dashboards |
| **CLI + Web** | `leap` CLI and FastAPI web API share the same logic |
| **Sharing** | Git-based distribution; optional community registry |
| **Polished UI** | Dark/light themes, sparklines, inline counts, academic fonts |

## Concepts

LEAP organizes work into **labs** and **experiments**:

- **Experiment** — A self-contained unit: Python functions in `funcs/`, optional UI in `ui/`, its own DuckDB database, and a `README.md` with frontmatter. Can be hosted independently on GitHub for sharing, but always runs inside a lab.
- **Lab** — A project root containing one or more experiments. Clone a lab, run `leap init`, and everything is ready.

```
my-lab/                      ← lab
├── README.md                # type: lab
├── config/
└── experiments/
    ├── sorting-viz/         ← experiment
    │   ├── README.md        # type: experiment
    │   ├── funcs/
    │   └── ui/
    └── graph-search/        ← experiment
```

## Creating & Sharing Experiments

You must be inside an initialized lab (`leap init`) to add experiments.

```bash
leap add my-experiment                          # scaffold a new local experiment
leap add https://github.com/user/cool-lab.git   # install from Git
leap remove my-experiment                        # remove an experiment
```

`leap add <url>` clones the experiment, installs `requirements.txt`, tracks it in your lab's README, and adds it to `.gitignore`. Running it again on an already-installed experiment pulls updates.

**Sharing your work:** Push to GitHub and share the URL. Others install with `leap add <url>`.

## Decorators

```python
from leap import adminonly, nolog, noregcheck, ratelimit, withctx, ctx

@nolog                          # skip logging (high-frequency calls)
def step(dx): return dx * 2

@noregcheck                     # open to all, no registration required
def echo(x): return x

@adminonly                      # admin sessions only
def reset_data(): clear_all()

@ratelimit("10/minute")         # custom rate limit
def expensive(x): return run_sim(x)

@withctx                        # access caller context via ctx
def start():
    return {"student": ctx.student_id, "trial": ctx.trial}
```

Functions are hot-reloadable at runtime via the admin UI or `POST /exp/<name>/admin/reload`.

## CLI Reference

| Command | Purpose |
|---|---|
| `leap run` | Start the server |
| `leap init` | Set up a lab (idempotent) |
| `leap add <name\|url\|path>` | Add experiment (scaffold, clone, or copy) |
| `leap remove <name>` | Remove an experiment |
| `leap list` | List experiments |
| `leap validate <name>` | Validate experiment setup |
| `leap discover [--tag]` | Browse the community registry |
| `leap publish <name>` | Publish to the registry |
| `leap export <exp> [--format]` | Export logs (jsonlines or csv) |
| `leap set-password` | Set admin password |
| `leap add-student <exp> <id>` | Add a student |
| `leap import-students <exp> <csv>` | Bulk-import students from CSV |
| `leap list-students <exp>` | List students |
| `leap config` | Show resolved configuration |
| `leap doctor` | Validate setup, resolve mismatches |
| `leap version` | Show version |

## Documentation

Full documentation is available at [github.com/leaplive/LEAP2](https://github.com/leaplive/LEAP2).

## Citation

If you use LEAP in your work, please cite our poster presented at **ACM SIGCSE TS 2026**:

> Sumedh Karajagi, Sampad Bhusan Mohanty, and Bhaskar Krishnamachari. 2026. **LEAP -- Live Experiments for Active Pedagogy.** In *Proceedings of the 57th ACM Technical Symposium on Computer Science Education (SIGCSE TS 2026)*. ACM. DOI: [10.1145/3770761.3777313](https://doi.org/10.1145/3770761.3777313)

```bibtex
@inproceedings{karajagi2026leap,
      title={LEAP -- Live Experiments for Active Pedagogy},
      author={Sumedh Karajagi and Sampad Bhusan Mohanty and Bhaskar Krishnamachari},
      booktitle={Proceedings of the 57th ACM Technical Symposium on Computer Science Education (SIGCSE TS 2026)},
      year={2026},
      publisher={ACM},
      doi={10.1145/3770761.3777313},
      eprint={2601.22534},
      archivePrefix={arXiv},
      url={https://arxiv.org/abs/2601.22534},
}
```

## License

MIT
