"""Root-level API: experiments list, health, functions, is-registered, login/logout."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from leap.api.deps import get_db_session, get_experiment_info, limiter
from leap.core import auth, storage
from leap.core.experiment import ExperimentInfo
from leap import __version__

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/experiments")
async def list_experiments(request: Request):
    experiments = request.app.state.experiments
    result = []
    for exp in experiments.values():
        meta = exp.to_metadata()
        try:
            session = storage.get_session(exp.name, exp.db_path)
            try:
                meta["student_count"] = storage.count_students(session)
            finally:
                session.close()
        except Exception:
            logger.exception("Failed to count students for experiment '%s'", exp.name)
            meta["student_count"] = 0
        result.append(meta)
    return {"experiments": result}


@router.get("/api/health")
async def health(request: Request):
    experiments = getattr(request.app.state, "experiments", {})
    exp_status = {}
    all_ok = True
    for name, exp in experiments.items():
        try:
            session = storage.get_session(exp.name, exp.db_path)
            try:
                exp_status[name] = {
                    "ok": True,
                    "students": storage.count_students(session),
                    "logs": storage.count_logs(session),
                    "db_path": str(exp.db_path),
                }
            finally:
                session.close()
        except Exception:
            logger.exception("Health check failed for experiment '%s'", name)
            all_ok = False
            exp_status[name] = {"ok": False, "error": "db_unreachable"}
    lab_info = getattr(request.app.state, "lab_info", {})
    return {
        "ok": all_ok,
        "version": __version__,
        "lab": lab_info,
        "experiment_count": len(experiments),
        "experiments": exp_status,
    }


@router.get("/exp/{experiment}/functions")
async def list_functions(
    exp_info: ExperimentInfo = Depends(get_experiment_info),
):
    return exp_info.get_functions_info()


@router.get("/exp/{experiment}/readme")
async def get_readme(
    exp_info: ExperimentInfo = Depends(get_experiment_info),
):
    try:
        text = exp_info.readme_path.read_text(encoding="utf-8")
    except OSError:
        raise HTTPException(404, detail="No README found for this experiment")
    frontmatter = dict(exp_info.frontmatter)
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            body = parts[2].strip()
    return {"frontmatter": frontmatter, "body": body}


@router.get("/exp/{experiment}/is-registered")
async def is_registered(
    student_id: str = Query(...),
    session: Session = Depends(get_db_session),
):
    registered = storage.is_registered(session, student_id)
    return {"registered": registered}


class LoginRequest(BaseModel):
    password: str


@router.post("/login")
@limiter.limit("5/minute")
async def login(body: LoginRequest, request: Request):
    root = getattr(request.app.state, "root", None)
    cred = auth.load_credentials(root)
    if not cred:
        raise HTTPException(500, detail="No admin credentials configured")
    if not auth.verify_password(body.password, cred):
        raise HTTPException(401, detail="Invalid password")
    request.session["admin"] = True
    return {"ok": True}


@router.get("/api/auth-status")
async def auth_status(request: Request):
    is_admin = request.session.get("admin", False)
    return {"admin": bool(is_admin)}


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return {"ok": True}
