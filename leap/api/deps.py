"""Shared FastAPI dependencies for experiment resolution and DB sessions."""

from __future__ import annotations

import os
from collections.abc import Generator

from fastapi import Depends, HTTPException, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from leap.core import storage
from leap.core.experiment import ExperimentInfo

limiter = Limiter(
    key_func=get_remote_address,
    enabled=os.environ.get("LEAP_RATE_LIMIT", "1") != "0",
)


async def get_experiment_info(experiment: str, request: Request) -> ExperimentInfo:
    experiments = request.app.state.experiments
    if experiment not in experiments:
        raise HTTPException(404, detail=f"Experiment '{experiment}' not found")
    return experiments[experiment]


def get_db_session(
    exp_info: ExperimentInfo = Depends(get_experiment_info),
) -> Generator[Session]:
    session = storage.get_session(exp_info.name, exp_info.db_path)
    try:
        yield session
    finally:
        session.close()
