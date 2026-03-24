"""Per-experiment DuckDB storage layer using SQLAlchemy 2.0 ORM."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import (
    String,
    Text,
    Integer,
    DateTime,
    Sequence,
    create_engine,
    select,
    delete,
    text,
    func as sa_func,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    Session,
    sessionmaker,
)

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


class Student(Base):
    __tablename__ = "students"

    student_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str | None] = mapped_column(String, nullable=True)


log_id_seq = Sequence("log_id_seq")


class Log(Base):
    __tablename__ = "logs"

    id: Mapped[int] = mapped_column(
        Integer, log_id_seq, server_default=log_id_seq.next_value(), primary_key=True
    )
    ts: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    student_id: Mapped[str] = mapped_column(String, nullable=False)
    experiment: Mapped[str] = mapped_column(String, nullable=False)
    trial: Mapped[str | None] = mapped_column(String, nullable=True)
    func_name: Mapped[str] = mapped_column(String, nullable=False)
    args_json: Mapped[str] = mapped_column(Text, nullable=False)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


_engines: dict[str, Any] = {}
_session_factories: dict[str, sessionmaker] = {}

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_logs_ts ON logs (ts)",
    "CREATE INDEX IF NOT EXISTS ix_logs_student_id ON logs (student_id)",
    "CREATE INDEX IF NOT EXISTS ix_logs_experiment ON logs (experiment)",
    "CREATE INDEX IF NOT EXISTS ix_logs_func_name ON logs (func_name)",
    "CREATE INDEX IF NOT EXISTS ix_logs_student_func ON logs (student_id, func_name)",
]


def _db_url(db_path: Path) -> str:
    return f"duckdb:///{db_path}"


def get_engine(experiment_name: str, db_path: Path):
    key = str(db_path)
    if key not in _engines:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(_db_url(db_path))
        Base.metadata.create_all(engine)
        with engine.connect() as conn:
            for idx_sql in _CREATE_INDEXES:
                conn.execute(text(idx_sql))
            conn.commit()
        _engines[key] = engine
        logger.info("Initialized DB for experiment '%s' at %s", experiment_name, db_path)
    return _engines[key]


def get_session(experiment_name: str, db_path: Path) -> Session:
    key = str(db_path)
    if key not in _session_factories:
        engine = get_engine(experiment_name, db_path)
        _session_factories[key] = sessionmaker(bind=engine)
    return _session_factories[key]()


def close_all_engines():
    for engine in _engines.values():
        engine.dispose()
    _engines.clear()
    _session_factories.clear()


# ── Student CRUD ──


def add_student(session: Session, student_id: str, name: str, email: str | None = None) -> Student:
    existing = session.get(Student, student_id)
    if existing:
        raise ValueError(f"Student '{student_id}' already exists")
    student = Student(student_id=student_id, name=name, email=email)
    session.add(student)
    session.commit()
    return student


def bulk_add_students(session: Session, students: list[dict]) -> dict:
    added, skipped, errors = [], [], []
    for row in students:
        sid = (row.get("student_id") or "").strip()
        name = (row.get("name") or "").strip() or sid
        email = (row.get("email") or "").strip() or None
        if not sid:
            errors.append({"student_id": sid, "error": "missing student_id"})
            continue
        if session.get(Student, sid):
            skipped.append(sid)
            continue
        session.add(Student(student_id=sid, name=name, email=email))
        added.append(sid)
    session.commit()
    return {"added": added, "skipped": skipped, "errors": errors}


def list_students(session: Session) -> list[dict]:
    stmt = select(Student).order_by(Student.student_id)
    return [
        {"student_id": s.student_id, "name": s.name, "email": s.email}
        for s in session.scalars(stmt)
    ]


def delete_student(session: Session, student_id: str) -> bool:
    student = session.get(Student, student_id)
    if not student:
        return False
    session.execute(delete(Log).where(Log.student_id == student_id))
    session.delete(student)
    session.commit()
    return True


def is_registered(session: Session, student_id: str) -> bool:
    return session.get(Student, student_id) is not None


def count_students(session: Session) -> int:
    return session.scalar(select(sa_func.count()).select_from(Student)) or 0


def count_logs(session: Session) -> int:
    return session.scalar(select(sa_func.count()).select_from(Log)) or 0


# ── Log CRUD ──


def add_log(
    session: Session,
    *,
    student_id: str,
    experiment: str,
    func_name: str,
    args: Any,
    result: Any = None,
    error: str | None = None,
    trial: str | None = None,
) -> Log:
    log = Log(
        ts=datetime.now(timezone.utc),
        student_id=student_id,
        experiment=experiment,
        func_name=func_name,
        args_json=json.dumps(args, default=str),
        result_json=json.dumps(result, default=str) if result is not None else None,
        error=error,
        trial=trial,
    )
    session.add(log)
    session.commit()
    return log


def _parse_json_safe(raw: str | None):
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Failed to parse stored JSON: %.100s", raw)
        return raw


def log_to_dict(log: Log) -> dict:
    return {
        "id": log.id,
        "ts": log.ts.isoformat() + "Z" if log.ts else None,
        "student_id": log.student_id,
        "experiment": log.experiment,
        "trial": log.trial,
        "func_name": log.func_name,
        "args": _parse_json_safe(log.args_json),
        "result": _parse_json_safe(log.result_json),
        "error": log.error,
    }


def query_logs(
    session: Session,
    *,
    student_id: str | None = None,
    trial: str | None = None,
    func_name: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    n: int = 100,
    order: str = "latest",
    after_id: int | None = None,
) -> list[dict]:
    n = max(1, min(n, 10_000))
    stmt = select(Log)

    if student_id:
        stmt = stmt.where(Log.student_id == student_id)
    if trial:
        stmt = stmt.where(Log.trial == trial)
    if func_name:
        stmt = stmt.where(Log.func_name == func_name)
    if start_time:
        stmt = stmt.where(Log.ts >= start_time)
    if end_time:
        stmt = stmt.where(Log.ts <= end_time)

    if order == "latest":
        if after_id is not None:
            stmt = stmt.where(Log.id < after_id)
        stmt = stmt.order_by(Log.id.desc())
    else:
        if after_id is not None:
            stmt = stmt.where(Log.id > after_id)
        stmt = stmt.order_by(Log.id.asc())

    stmt = stmt.limit(n)
    return [log_to_dict(log) for log in session.scalars(stmt)]


def delete_log(session: Session, log_id: int) -> bool:
    """Delete a single log by id. Returns True if deleted, False if not found."""
    log = session.get(Log, log_id)
    if not log:
        return False
    session.delete(log)
    session.commit()
    return True


def delete_logs(
    session: Session,
    student_id: str | None = None,
    trial: str | None = None,
    func_name: str | None = None,
) -> int:
    """Delete logs matching the given filters. Returns count of deleted rows."""
    conditions = []
    if student_id is not None:
        conditions.append(Log.student_id == student_id)
    if trial is not None:
        conditions.append(Log.trial == trial)
    if func_name is not None:
        conditions.append(Log.func_name == func_name)
    count_stmt = select(sa_func.count()).select_from(Log)
    del_stmt = delete(Log)
    for cond in conditions:
        count_stmt = count_stmt.where(cond)
        del_stmt = del_stmt.where(cond)
    count = session.scalar(count_stmt)
    session.execute(del_stmt)
    session.commit()
    return count


def get_log_options(session: Session) -> dict:
    students = [
        row[0] for row in session.execute(
            select(Student.student_id).order_by(Student.student_id)
        )
    ]
    trials = [
        row[0] for row in session.execute(
            select(Log.trial).where(Log.trial.isnot(None)).distinct().order_by(Log.trial)
        )
    ]
    return {"students": students, "trials": trials, "log_count": count_logs(session)}
