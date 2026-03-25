"""FastAPI application assembly and startup."""

from __future__ import annotations

import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException as StarletteHTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from starlette.staticfiles import StaticFiles

from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from leap import __version__
from leap.api.deps import limiter
from leap.config import get_root, ui_dir, package_ui_dir, SESSION_SECRET_KEY, DEFAULT_EXPERIMENT
from leap.core.auth import ensure_credentials
from leap.core.experiment import discover_experiments, parse_frontmatter, ENTRY_POINT_README
from leap.api import call, logs, admin, experiments
from leap.core import storage

logger = logging.getLogger(__name__)


def create_app(root=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        resolved_root = root or get_root()
        app.state.root = resolved_root
        logger.info("LEAP2 root: %s", resolved_root)

        ensure_credentials(resolved_root)

        exps = discover_experiments(resolved_root)
        app.state.experiments = exps
        logger.info("Loaded %d experiment(s): %s", len(exps), ", ".join(exps.keys()) or "(none)")

        pkg_ui = package_ui_dir()
        pkg_shared = pkg_ui / "shared"
        if pkg_shared.is_dir():
            app.mount("/static", StaticFiles(directory=str(pkg_shared)), name="static-assets")
            logger.info("Mounted /static -> %s", pkg_shared)

        for exp_name, exp_info in exps.items():
            if exp_info.ui_dir.is_dir():
                mount_path = f"/exp/{exp_name}/ui"
                app.mount(mount_path, StaticFiles(directory=str(exp_info.ui_dir)), name=f"ui-{exp_name}")
                logger.info("Mounted %s -> %s", mount_path, exp_info.ui_dir)

        assets_dir = resolved_root / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="project-assets")
            logger.info("Mounted /assets -> %s", assets_dir)

        _LAB_FIELDS = {"name": "", "display_name": "", "icons": [], "description": "", "authors": [], "organizations": [], "tags": [], "repository": ""}
        root_readme = resolved_root / "README.md"
        fm = parse_frontmatter(root_readme) if root_readme.is_file() else {}
        lab_info = {k: fm.get(k, default) for k, default in _LAB_FIELDS.items()}
        lab_info["display_name"] = lab_info["display_name"] or lab_info["name"]
        # Normalize list fields (accept both string and array in YAML)
        from leap.core.experiment import _as_list
        for k in ("authors", "organizations", "icons"):
            lab_info[k] = _as_list(lab_info.get(k, fm.get(k[:-1], [])))
        app.state.lab_info = lab_info

        app.state.ui_root = ui_dir(resolved_root)
        app.state.pkg_ui_root = pkg_ui
        yield
        storage.close_all_engines()

    app = FastAPI(title="LEAP2", version=__version__, lifespan=lifespan)

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    secret = SESSION_SECRET_KEY or secrets.token_hex(32)
    app.add_middleware(SessionMiddleware, secret_key=secret, max_age=86400, same_site="strict")

    cors_origins = os.environ.get("CORS_ORIGINS", "")
    if cors_origins:
        origins = [o.strip() for o in cors_origins.split(",") if o.strip()]
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.include_router(call.router)
    app.include_router(logs.router)
    app.include_router(admin.router)
    app.include_router(experiments.router)

    @app.get("/", include_in_schema=False)
    async def landing(request: Request):
        exps = getattr(request.app.state, "experiments", {})
        if DEFAULT_EXPERIMENT and DEFAULT_EXPERIMENT in exps:
            entry = exps[DEFAULT_EXPERIMENT].entry_point
            if entry == ENTRY_POINT_README:
                url = f"/static/readme.html?exp={DEFAULT_EXPERIMENT}"
            else:
                url = f"/exp/{DEFAULT_EXPERIMENT}/ui/{entry}"
            return RedirectResponse(url=url, status_code=307)

        for ui_root in [getattr(request.app.state, "ui_root", Path()),
                        getattr(request.app.state, "pkg_ui_root", Path())]:
            landing_file = ui_root / "landing" / "index.html"
            if landing_file.is_file():
                return FileResponse(str(landing_file), media_type="text/html")
        return {"message": "LEAP2 is running. No landing page found."}

    @app.get("/login", include_in_schema=False)
    async def login_page(request: Request):
        # Login is handled via modal — redirect to landing page
        return RedirectResponse(url="/", status_code=307)

    @app.exception_handler(404)
    async def not_found_handler(request: Request, exc: StarletteHTTPException):
        if request.url.path.startswith("/api/") or (request.url.path.startswith("/exp/") and "/ui/" not in request.url.path):
            detail = getattr(exc, "detail", None)
            if isinstance(detail, str) and detail.strip():
                msg = detail
            else:
                msg = "Not found"
            return JSONResponse(status_code=404, content={"detail": msg})
        for ui_root in [getattr(request.app.state, "ui_root", Path()),
                        getattr(request.app.state, "pkg_ui_root", Path())]:
            page_404 = ui_root / "404.html"
            if page_404.is_file():
                return FileResponse(str(page_404), status_code=404, media_type="text/html")
        return HTMLResponse("<h1>404 — Not Found</h1>", status_code=404)

    return app


app = create_app()
