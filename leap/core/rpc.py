"""RPC execution, logging, and function decorators."""

from __future__ import annotations

import contextvars
import logging
import os
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

from leap.core import storage

logger = logging.getLogger(__name__)

STUDENT_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,255}$")

DEFAULT_RATE_LIMIT = "120/minute"


def ratelimit(limit):
    """Decorator: set per-student rate limit. Pass a string like "10/minute" or False to disable."""
    def decorator(func):
        func._leap_ratelimit = limit
        return func
    return decorator


class RateLimitError(Exception):
    """Raised when a per-function rate limit is exceeded."""


_rate_windows: dict[tuple, deque[float]] = defaultdict(deque)
_PERIODS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}
_parsed_limits: dict[str, tuple[int, int]] = {}
_SWEEP_INTERVAL = 300  # seconds between stale key sweeps
_last_sweep: float = 0.0


def _parse_limit(limit_str: str) -> tuple[int, int]:
    cached = _parsed_limits.get(limit_str)
    if cached:
        return cached
    count, period = limit_str.strip().split("/")
    result = int(count), _PERIODS[period]
    _parsed_limits[limit_str] = result
    return result


def _check_rate_limit(key: tuple, limit_str: str) -> bool:
    global _last_sweep
    max_calls, window = _parse_limit(limit_str)
    now = time.monotonic()

    # Periodic sweep of stale keys
    if now - _last_sweep > _SWEEP_INTERVAL:
        _last_sweep = now
        stale = [k for k, ts in _rate_windows.items() if not ts or ts[-1] < now - window]
        for k in stale:
            del _rate_windows[k]

    timestamps = _rate_windows[key]
    cutoff = now - window
    while timestamps and timestamps[0] <= cutoff:
        timestamps.popleft()
    if len(timestamps) >= max_calls:
        return False
    timestamps.append(now)
    return True


def nolog(func):
    """Decorator: skip logging for this function (high-frequency calls)."""
    func._leap_nolog = True
    return func


def noregcheck(func):
    """Decorator: skip registration check for this function."""
    func._leap_noregcheck = True
    return func


def adminonly(func):
    """Decorator: restrict this function to admin sessions only."""
    func._leap_adminonly = True
    return func


@dataclass
class Context:
    """Call metadata available to @withctx functions via ``from leap import ctx``."""
    student_id: str
    trial: str | None
    experiment: str


_ctx_var: contextvars.ContextVar[Context] = contextvars.ContextVar("leap_ctx")


class _CtxProxy:
    """Proxy that forwards attribute access to the current request's Context."""
    def __getattr__(self, name: str) -> Any:
        return getattr(_ctx_var.get(), name)


ctx = _CtxProxy()


def withctx(func):
    """Decorator: populates ``leap.ctx`` with call metadata (student_id, trial, experiment)."""
    func._leap_withctx = True
    return func


def _has_flag(func, flag: str) -> bool:
    return getattr(func, flag, False)


def validate_student_id(student_id: str) -> bool:
    return bool(STUDENT_ID_RE.match(student_id))


def is_lightweight(func, experiment) -> bool:
    """True if the function skips all DB operations (no logging, no reg check, no rate limit)."""
    return (
        _has_flag(func, "_leap_nolog")
        and (_has_flag(func, "_leap_noregcheck") or not experiment.require_registration)
    )


def execute_rpc(
    experiment,  # ExperimentInfo
    session=None,
    *,
    func_name: str,
    args: list | None = None,
    kwargs: dict | None = None,
    student_id: str,
    trial: str | None = None,
) -> Any:
    """Execute an RPC call: validate, run function, log result."""
    if func_name not in experiment.functions:
        raise ValueError(f"Unknown function: '{func_name}'")

    func = experiment.functions[func_name]

    if not validate_student_id(student_id):
        raise ValueError(f"Invalid student_id: '{student_id}'")

    # Lazy DB session — only create when the function actually needs it
    skip_regcheck = _has_flag(func, "_leap_noregcheck")
    skip_log = _has_flag(func, "_leap_nolog")
    needs_db = (
        (not skip_regcheck and experiment.require_registration)
        or not skip_log
    )
    own_session = False
    if needs_db and session is None:
        session = storage.get_session(experiment.name, experiment.db_path)
        own_session = True

    try:
        if not skip_regcheck and experiment.require_registration:
            if not storage.is_registered(session, student_id):
                raise PermissionError(f"Student '{student_id}' is not registered")

        env_limit = os.environ.get("LEAP_RATE_LIMIT")
        if env_limit != "0":
            limit_val = getattr(func, "_leap_ratelimit", "default")
            if limit_val == "default":
                limit_val = DEFAULT_RATE_LIMIT
            if limit_val:
                key = (experiment.name, func_name, student_id)
                if not _check_rate_limit(key, limit_val):
                    raise RateLimitError(f"Rate limit exceeded for '{func_name}': {limit_val}")

        args = args or []
        kwargs = kwargs or {}
        error_msg = None
        result = None

        if _has_flag(func, "_leap_withctx"):
            _ctx_var.set(Context(student_id=student_id, trial=trial, experiment=experiment.name))

        try:
            result = func(*args, **kwargs)
        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"
            logger.exception("RPC %s.%s raised: %s", experiment.name, func_name, error_msg)

        if not skip_log:
            try:
                storage.add_log(
                    session,
                    student_id=student_id,
                    experiment=experiment.name,
                    func_name=func_name,
                    args=args,
                    result=result,
                    error=error_msg,
                    trial=trial,
                )
            except Exception:
                logger.exception("Failed to log RPC call %s.%s", experiment.name, func_name)

        if error_msg:
            raise RuntimeError(error_msg)

        return result
    finally:
        if own_session and session is not None:
            session.close()
