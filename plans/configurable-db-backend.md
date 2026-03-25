# Configurable Database Backend (DuckDB / SQLite)

## Context

DuckDB is LEAP2's sole database backend. Its process-level write lock serializes all log inserts, making it the throughput bottleneck under concurrent load (as shown by the benchmark experiment). SQLite with WAL mode handles concurrent writes better for simple append workloads. Different experiments have different needs — a benchmark or analytics-heavy experiment benefits from DuckDB's columnar engine, while a simple quiz or game experiment just needs fast writes.

The storage layer (`storage.py`) already uses SQLAlchemy ORM, so the DB is abstracted behind a URL string. Switching backends is mostly a URL swap, with one compatibility fix for the `Sequence` used on log IDs.

**Note:** A performance/production plan was referenced in the benchmark README at `plans/performance-production-plan.md` but does not exist yet. This database backend work should be incorporated into that plan when it's created.

## Design

### Configuration hierarchy

```
Default (duckdb) → Lab README `db:` field → Experiment README `db:` field
```

- **Default**: `duckdb` (no change for existing labs)
- **Lab-level**: `db: sqlite` in lab root README frontmatter sets default for all experiments
- **Experiment-level**: `db: sqlite` in experiment README frontmatter overrides lab default
- Valid values: `duckdb`, `sqlite`

### Compatibility concern: Sequence

`storage.py` uses `Sequence("log_id_seq")` with `server_default=log_id_seq.next_value()` for the `Log.id` column. This works with DuckDB and PostgreSQL but **not SQLite** (SQLite ignores sequences and uses ROWID/AUTOINCREMENT).

Fix: Use SQLAlchemy's `Identity()` or conditional column definition. Simplest approach: drop the explicit `Sequence` and use `mapped_column(Integer, primary_key=True, autoincrement=True)` which works universally across all SQLAlchemy backends.

### SQLite WAL mode

When creating a SQLite engine, execute `PRAGMA journal_mode=WAL` and `PRAGMA synchronous=NORMAL` for better concurrent write performance. This is a one-time setup per engine.

## Changes

### 1. `leap/core/experiment.py`

**`DEFAULT_FRONTMATTER`**: Add `"db": ""` (empty string = inherit from lab or use default)

**`_apply_frontmatter`**: Add `self.db_backend = fm.get("db", "").strip().lower() or ""`

**`to_metadata`**: Add `"db": self.db_backend`

### 2. `leap/main.py`

**`_LAB_FIELDS`**: Add `"db": ""`

**After building `lab_info`**: Store lab-level db preference in `app.state.lab_db_backend`

### 3. `leap/core/storage.py`

**Remove `Sequence`**: Change `Log.id` from:
```python
log_id_seq = Sequence("log_id_seq")
class Log(Base):
    id: Mapped[int] = mapped_column(
        Integer, log_id_seq, server_default=log_id_seq.next_value(), primary_key=True
    )
```
To:
```python
class Log(Base):
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
```

**Add `_db_url` backend dispatch**:
```python
_BACKENDS = {"duckdb", "sqlite"}

def _db_url(db_path: Path, backend: str = "duckdb") -> str:
    if backend == "sqlite":
        return f"sqlite:///{db_path}"
    return f"duckdb:///{db_path}"
```

**Update `get_engine`** signature to accept `backend` parameter:
```python
def get_engine(experiment_name: str, db_path: Path, backend: str = "duckdb"):
    ...
    engine = create_engine(_db_url(db_path, backend))
    if backend == "sqlite":
        with engine.connect() as conn:
            conn.execute(text("PRAGMA journal_mode=WAL"))
            conn.execute(text("PRAGMA synchronous=NORMAL"))
            conn.commit()
    ...
```

**Update `get_session`** signature similarly.

**Update docstring**: "Per-experiment storage layer (DuckDB / SQLite) using SQLAlchemy 2.0 ORM."

### 4. `leap/api/deps.py`

**`get_db_session`**: Resolve the effective backend for the experiment:
```python
def get_db_session(
    exp_info: ExperimentInfo = Depends(get_experiment_info),
    request: Request,
) -> Generator[Session]:
    backend = exp_info.db_backend or getattr(request.app.state, "lab_db_backend", "") or "duckdb"
    session = storage.get_session(exp_info.name, exp_info.db_path, backend)
    ...
```

### 5. `leap/core/rpc.py`

Find where `storage.get_session` is called in the RPC path and pass `backend` through. The lazy session creation in `execute_rpc` needs the backend parameter.

### 6. `leap/cli.py`

**CLI session calls**: Any CLI code that calls `storage.get_session` or `storage.get_engine` needs to read the experiment's `db_backend` and pass it through.

**`leap new` prompt**: No need to prompt for DB backend during experiment creation — it's an advanced setting users can add to frontmatter manually.

### 7. `pyproject.toml`

No change needed — `sqlalchemy>=2.0` already includes SQLite support (it's built into Python's stdlib). The `duckdb-engine` dependency stays for DuckDB support.

## Files

- `leap/core/storage.py` — backend dispatch, remove Sequence, SQLite WAL pragma
- `leap/core/experiment.py` — `db` frontmatter field, `db_backend` attribute
- `leap/main.py` — lab-level `db` field, `app.state.lab_db_backend`
- `leap/api/deps.py` — resolve effective backend per request
- `leap/core/rpc.py` — pass backend through to storage calls
- `leap/cli.py` — pass backend through CLI storage calls

## Verification

1. **Default (no config)**: `leap run` with no `db:` field → uses DuckDB as before
2. **Experiment-level SQLite**: Add `db: sqlite` to one experiment's README → that experiment's DB file is SQLite, others stay DuckDB
3. **Lab-level SQLite**: Add `db: sqlite` to lab root README → all experiments default to SQLite
4. **Override**: Lab sets `db: sqlite`, one experiment sets `db: duckdb` → that experiment uses DuckDB
5. **Benchmark**: Run `python -m pytest tests/test_benchmark.py -v` with both backends to compare throughput
6. **Invalid value**: `db: postgres` → warn and fall back to DuckDB
7. **Existing DBs**: Switching backend on an experiment with existing data creates a new DB file (old `.db` file untouched) — no migration
