"""Phase 4 tests: shared UI pages serving, CORS middleware."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from leap.main import create_app
from leap.core import storage


@pytest.fixture
def client(tmp_credentials: Path):
    app = create_app(root=tmp_credentials)
    with TestClient(app) as c:
        yield c
    storage.close_all_engines()


class TestSharedPages:
    def test_students_html(self, client):
        resp = client.get("/static/students.html")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_students_html_content(self, client):
        resp = client.get("/static/students.html")
        assert "Students" in resp.text

    def test_logs_html(self, client):
        resp = client.get("/static/logs.html")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_logs_html_content(self, client):
        resp = client.get("/static/logs.html")
        assert "Logs" in resp.text


class TestFunctionsPage:
    def test_functions_html(self, client):
        resp = client.get("/static/functions.html")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_functions_html_content(self, client):
        resp = client.get("/static/functions.html")
        assert "Functions" in resp.text


class TestFunctionsAPIFlags:
    def test_functions_include_nolog_flag(self, client):
        resp = client.get("/exp/default/functions")
        assert resp.status_code == 200
        funcs = resp.json()
        nolog_funcs = [n for n, info in funcs.items() if info.get("nolog")]
        assert len(nolog_funcs) > 0

    def test_functions_include_noregcheck_flag(self, client):
        resp = client.get("/exp/default/functions")
        funcs = resp.json()
        noregcheck_funcs = [n for n, info in funcs.items() if info.get("noregcheck")]
        assert len(noregcheck_funcs) > 0

    def test_functions_include_adminonly_flag(self, client):
        resp = client.get("/exp/default/functions")
        funcs = resp.json()
        adminonly_funcs = [n for n, info in funcs.items() if info.get("adminonly")]
        assert len(adminonly_funcs) > 0

    def test_normal_function_flags_false(self, client):
        resp = client.get("/exp/default/functions")
        funcs = resp.json()
        assert "square" in funcs
        assert funcs["square"]["nolog"] is False
        assert funcs["square"]["noregcheck"] is False
        assert funcs["square"]["adminonly"] is False

    def test_all_functions_have_flag_fields(self, client):
        resp = client.get("/exp/default/functions")
        funcs = resp.json()
        for name, info in funcs.items():
            assert "nolog" in info, f"{name} missing nolog"
            assert "noregcheck" in info, f"{name} missing noregcheck"
            assert "adminonly" in info, f"{name} missing adminonly"


class TestCORSDisabledByDefault:
    def test_no_cors_headers_by_default(self, client):
        resp = client.get("/api/health", headers={"Origin": "http://evil.com"})
        assert "access-control-allow-origin" not in resp.headers


class TestCORSEnabled:
    def test_cors_headers_when_configured(self, tmp_credentials):
        with patch.dict(os.environ, {"CORS_ORIGINS": "http://localhost:3000"}):
            app = create_app(root=tmp_credentials)
            with TestClient(app) as c:
                resp = c.get(
                    "/api/health",
                    headers={"Origin": "http://localhost:3000"},
                )
                assert resp.headers.get("access-control-allow-origin") == "http://localhost:3000"
            storage.close_all_engines()

    def test_cors_preflight(self, tmp_credentials):
        with patch.dict(os.environ, {"CORS_ORIGINS": "http://localhost:3000"}):
            app = create_app(root=tmp_credentials)
            with TestClient(app) as c:
                resp = c.options(
                    "/api/health",
                    headers={
                        "Origin": "http://localhost:3000",
                        "Access-Control-Request-Method": "GET",
                    },
                )
                assert resp.status_code == 200
                assert "access-control-allow-origin" in resp.headers
            storage.close_all_engines()

    def test_cors_rejects_unlisted_origin(self, tmp_credentials):
        with patch.dict(os.environ, {"CORS_ORIGINS": "http://localhost:3000"}):
            app = create_app(root=tmp_credentials)
            with TestClient(app) as c:
                resp = c.get(
                    "/api/health",
                    headers={"Origin": "http://evil.com"},
                )
                assert resp.headers.get("access-control-allow-origin") != "http://evil.com"
            storage.close_all_engines()

    def test_cors_multiple_origins(self, tmp_credentials):
        with patch.dict(os.environ, {"CORS_ORIGINS": "http://a.com, http://b.com"}):
            app = create_app(root=tmp_credentials)
            with TestClient(app) as c:
                resp = c.get("/api/health", headers={"Origin": "http://b.com"})
                assert resp.headers.get("access-control-allow-origin") == "http://b.com"
            storage.close_all_engines()
