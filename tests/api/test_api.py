"""API integration tests using FastAPI TestClient."""

from __future__ import annotations

import json
import time
from pathlib import Path

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


@pytest.fixture
def admin_client(client: TestClient):
    """Client with admin session."""
    resp = client.post("/login", json={"password": "testpass"})
    assert resp.status_code == 200
    return client


def _register_student(admin_client, exp="default", sid="s001", name="Alice"):
    resp = admin_client.post(
        f"/exp/{exp}/admin/add-student",
        json={"student_id": sid, "name": name},
    )
    assert resp.status_code == 200
    return resp


def _call_func(client, exp="default", sid="s001", func="square", args=None, trial=None):
    body = {"student_id": sid, "func_name": func, "args": args or []}
    if trial:
        body["trial"] = trial
    return client.post(f"/exp/{exp}/call", json=body)


# ── Health & Metadata ──


class TestHealth:
    def test_health(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "version" in data

    def test_health_version_format(self, client):
        resp = client.get("/api/health")
        version = resp.json()["version"]
        parts = version.split(".")
        assert len(parts) == 3


class TestExperiments:
    def test_list_experiments(self, client):
        resp = client.get("/api/experiments")
        assert resp.status_code == 200
        exps = resp.json()["experiments"]
        assert isinstance(exps, list)
        assert any(e["name"] == "default" for e in exps)

    def test_experiment_metadata_shape(self, client):
        resp = client.get("/api/experiments")
        exp = resp.json()["experiments"][0]
        assert "name" in exp
        assert "display_name" in exp
        assert "description" in exp
        assert "entry_point" in exp
        assert "pages" in exp

    def test_experiment_metadata_pages_default_empty(self, client):
        """Experiments without pages config should return empty list."""
        resp = client.get("/api/experiments")
        exp = resp.json()["experiments"][0]
        assert exp["pages"] == []

    def test_functions_list(self, client):
        resp = client.get("/exp/default/functions")
        assert resp.status_code == 200
        data = resp.json()
        assert "square" in data
        assert "signature" in data["square"]
        assert "doc" in data["square"]

    def test_functions_list_has_add(self, client):
        resp = client.get("/exp/default/functions")
        data = resp.json()
        assert "add" in data

    def test_unknown_experiment_functions(self, client):
        resp = client.get("/exp/nonexistent/functions")
        assert resp.status_code == 404

    def test_unknown_experiment_logs(self, client):
        resp = client.get("/exp/nonexistent/logs")
        assert resp.status_code == 404

    def test_unknown_experiment_log_options(self, client):
        resp = client.get("/exp/nonexistent/log-options")
        assert resp.status_code == 404

    def test_unknown_experiment_call(self, client):
        resp = client.post("/exp/nonexistent/call", json={
            "student_id": "s001", "func_name": "f", "args": [],
        })
        assert resp.status_code == 404

    def test_unknown_experiment_is_registered(self, client):
        resp = client.get("/exp/nonexistent/is-registered", params={"student_id": "s001"})
        assert resp.status_code == 404


# ── Authentication ──


class TestAuth:
    def test_login_success(self, client):
        resp = client.post("/login", json={"password": "testpass"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_login_wrong_password(self, client):
        resp = client.post("/login", json={"password": "wrong"})
        assert resp.status_code == 401
        assert "detail" in resp.json()

    def test_admin_requires_auth(self, client):
        resp = client.get("/exp/default/admin/students")
        assert resp.status_code == 401

    def test_all_admin_endpoints_require_auth(self, client):
        assert client.get("/exp/default/admin/students").status_code == 401
        assert client.post("/exp/default/admin/add-student",
            json={"student_id": "x", "name": "x"}).status_code == 401
        assert client.post("/exp/default/admin/delete-student",
            json={"student_id": "x"}).status_code == 401
        assert client.post("/exp/default/admin/delete-log",
            json={"log_id": 1}).status_code == 401
        assert client.post("/exp/default/admin/reload").status_code == 401

    def test_logout(self, admin_client):
        resp = admin_client.post("/logout")
        assert resp.status_code == 200
        resp = admin_client.get("/exp/default/admin/students")
        assert resp.status_code == 401

    def test_auth_status_not_logged_in(self, client):
        resp = client.get("/api/auth-status")
        assert resp.status_code == 200
        assert resp.json()["admin"] is False

    def test_auth_status_logged_in(self, admin_client):
        resp = admin_client.get("/api/auth-status")
        assert resp.status_code == 200
        assert resp.json()["admin"] is True

    def test_auth_status_after_logout(self, admin_client):
        admin_client.post("/logout")
        resp = admin_client.get("/api/auth-status")
        assert resp.json()["admin"] is False

    def test_public_endpoints_no_auth(self, client):
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/experiments").status_code == 200
        assert client.get("/api/auth-status").status_code == 200
        assert client.get("/exp/default/functions").status_code == 200
        assert client.get("/exp/default/logs").status_code == 200
        assert client.get("/exp/default/log-options").status_code == 200
        assert client.get("/exp/default/is-registered",
            params={"student_id": "s001"}).status_code == 200


# ── Student Admin ──


class TestStudentAdmin:
    def test_add_and_list_students(self, admin_client):
        _register_student(admin_client)
        resp = admin_client.get("/exp/default/admin/students")
        students = resp.json()["students"]
        assert len(students) == 1
        assert students[0]["student_id"] == "s001"
        assert students[0]["name"] == "Alice"

    def test_add_student_with_email(self, admin_client):
        admin_client.post(
            "/exp/default/admin/add-student",
            json={"student_id": "s001", "name": "Alice", "email": "alice@u.edu"},
        )
        students = admin_client.get("/exp/default/admin/students").json()["students"]
        assert students[0]["email"] == "alice@u.edu"

    def test_add_multiple_students(self, admin_client):
        for i in range(5):
            _register_student(admin_client, sid=f"s{i:03d}", name=f"Student {i}")
        students = admin_client.get("/exp/default/admin/students").json()["students"]
        assert len(students) == 5

    def test_add_duplicate(self, admin_client):
        _register_student(admin_client)
        resp = admin_client.post(
            "/exp/default/admin/add-student",
            json={"student_id": "s001", "name": "Alice 2"},
        )
        assert resp.status_code == 409

    def test_delete_student(self, admin_client):
        _register_student(admin_client)
        resp = admin_client.post(
            "/exp/default/admin/delete-student",
            json={"student_id": "s001"},
        )
        assert resp.status_code == 200
        students = admin_client.get("/exp/default/admin/students").json()["students"]
        assert len(students) == 0

    def test_delete_nonexistent(self, admin_client):
        resp = admin_client.post(
            "/exp/default/admin/delete-student",
            json={"student_id": "nobody"},
        )
        assert resp.status_code == 404

    def test_delete_cascades_logs(self, admin_client):
        _register_student(admin_client)
        _call_func(admin_client, sid="s001", args=[7])
        assert len(admin_client.get("/exp/default/logs").json()["logs"]) == 1

        admin_client.post(
            "/exp/default/admin/delete-student",
            json={"student_id": "s001"},
        )
        assert len(admin_client.get("/exp/default/logs").json()["logs"]) == 0

    def test_empty_student_list(self, admin_client):
        resp = admin_client.get("/exp/default/admin/students")
        assert resp.json()["students"] == []

    def test_add_student_unknown_experiment(self, admin_client):
        resp = admin_client.post(
            "/exp/nonexistent/admin/add-student",
            json={"student_id": "s001", "name": "Alice"},
        )
        assert resp.status_code == 404


class TestImportStudents:
    def test_import_students(self, admin_client):
        resp = admin_client.post(
            "/exp/default/admin/import-students",
            json={"students": [
                {"student_id": "s001", "name": "Alice"},
                {"student_id": "s002", "name": "Bob"},
                {"student_id": "s003", "name": "Charlie"},
            ]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert len(data["added"]) == 3

    def test_import_skips_duplicates(self, admin_client):
        _register_student(admin_client, sid="s001", name="Alice")
        resp = admin_client.post(
            "/exp/default/admin/import-students",
            json={"students": [
                {"student_id": "s001", "name": "Alice"},
                {"student_id": "s002", "name": "Bob"},
            ]},
        )
        data = resp.json()
        assert data["added"] == ["s002"]
        assert data["skipped"] == ["s001"]

    def test_import_requires_auth(self, client):
        resp = client.post(
            "/exp/default/admin/import-students",
            json={"students": [{"student_id": "s001"}]},
        )
        assert resp.status_code == 401

    def test_import_unknown_experiment(self, admin_client):
        resp = admin_client.post(
            "/exp/nonexistent/admin/import-students",
            json={"students": [{"student_id": "s001"}]},
        )
        assert resp.status_code == 404


# ── Is Registered ──


class TestIsRegistered:
    def test_not_registered(self, client):
        resp = client.get("/exp/default/is-registered", params={"student_id": "s001"})
        assert resp.json()["registered"] is False

    def test_registered(self, admin_client):
        _register_student(admin_client)
        resp = admin_client.get("/exp/default/is-registered", params={"student_id": "s001"})
        assert resp.json()["registered"] is True

    def test_after_delete(self, admin_client):
        _register_student(admin_client)
        admin_client.post("/exp/default/admin/delete-student", json={"student_id": "s001"})
        resp = admin_client.get("/exp/default/is-registered", params={"student_id": "s001"})
        assert resp.json()["registered"] is False


# ── RPC Calls ──


class TestRPC:
    def test_call_function(self, admin_client):
        _register_student(admin_client)
        resp = _call_func(admin_client, args=[7])
        assert resp.status_code == 200
        assert resp.json()["result"] == 49

    def test_call_add_function(self, admin_client):
        _register_student(admin_client)
        resp = _call_func(admin_client, func="add", args=[3, 4])
        assert resp.status_code == 200
        assert resp.json()["result"] == 7

    def test_call_with_trial(self, admin_client):
        _register_student(admin_client)
        resp = _call_func(admin_client, args=[5], trial="run-1")
        assert resp.status_code == 200
        logs = admin_client.get("/exp/default/logs").json()["logs"]
        assert logs[0]["trial"] == "run-1"

    def test_call_unregistered(self, client):
        resp = _call_func(client, sid="nobody", args=[5])
        assert resp.status_code == 403

    def test_call_unknown_function(self, admin_client):
        _register_student(admin_client)
        resp = _call_func(admin_client, func="nope", args=[])
        assert resp.status_code == 400

    def test_call_response_shape(self, admin_client):
        _register_student(admin_client)
        resp = _call_func(admin_client, args=[3])
        data = resp.json()
        assert "result" in data
        assert data["result"] == 9

    def test_error_response_shape(self, client):
        resp = _call_func(client, sid="nobody", args=[5])
        data = resp.json()
        assert "detail" in data

    def test_multiple_calls(self, admin_client):
        _register_student(admin_client)
        for i in range(10):
            resp = _call_func(admin_client, args=[i])
            assert resp.status_code == 200
            assert resp.json()["result"] == i * i


# ── Logs API ──


class TestLogs:
    def test_logs_empty(self, client):
        resp = client.get("/exp/default/logs")
        assert resp.status_code == 200
        assert resp.json()["logs"] == []

    def test_logs_after_call(self, admin_client):
        _register_student(admin_client)
        _call_func(admin_client, args=[7], trial="test-run")
        resp = admin_client.get("/exp/default/logs")
        logs = resp.json()["logs"]
        assert len(logs) == 1
        assert logs[0]["func_name"] == "square"
        assert logs[0]["args"] == [7]
        assert logs[0]["result"] == 49
        assert logs[0]["trial"] == "test-run"

    def test_log_entry_shape(self, admin_client):
        _register_student(admin_client)
        _call_func(admin_client, args=[2])
        logs = admin_client.get("/exp/default/logs").json()["logs"]
        log = logs[0]
        expected_keys = {"id", "ts", "student_id", "experiment", "trial", "func_name", "args", "result", "error"}
        assert set(log.keys()) == expected_keys
        assert log["ts"].endswith("Z")

    def test_log_filter_by_student(self, admin_client):
        _register_student(admin_client, sid="s001", name="Alice")
        _register_student(admin_client, sid="s002", name="Bob")
        _call_func(admin_client, sid="s001", args=[1])
        _call_func(admin_client, sid="s002", args=[2])

        resp = admin_client.get("/exp/default/logs", params={"student_id": "s001"})
        logs = resp.json()["logs"]
        assert len(logs) == 1
        assert logs[0]["student_id"] == "s001"

    def test_log_filter_by_func_name(self, admin_client):
        _register_student(admin_client)
        _call_func(admin_client, func="square", args=[2])
        _call_func(admin_client, func="add", args=[1, 2])

        resp = admin_client.get("/exp/default/logs", params={"func_name": "square"})
        logs = resp.json()["logs"]
        assert len(logs) == 1
        assert logs[0]["func_name"] == "square"

    def test_log_filter_by_trial(self, admin_client):
        _register_student(admin_client)
        _call_func(admin_client, args=[1], trial="alpha")
        _call_func(admin_client, args=[2], trial="beta")
        _call_func(admin_client, args=[3])

        resp = admin_client.get("/exp/default/logs", params={"trial_name": "alpha"})
        logs = resp.json()["logs"]
        assert len(logs) == 1
        assert logs[0]["trial"] == "alpha"

    def test_log_filter_unknown_func_name(self, admin_client):
        resp = admin_client.get("/exp/default/logs", params={"func_name": "nonexistent"})
        assert resp.status_code == 400

    def test_log_order_latest(self, admin_client):
        _register_student(admin_client)
        for i in range(5):
            _call_func(admin_client, args=[i])
        resp = admin_client.get("/exp/default/logs", params={"order": "latest", "n": 3})
        logs = resp.json()["logs"]
        assert len(logs) == 3
        assert logs[0]["id"] > logs[1]["id"] > logs[2]["id"]

    def test_log_order_earliest(self, admin_client):
        _register_student(admin_client)
        for i in range(5):
            _call_func(admin_client, args=[i])
        resp = admin_client.get("/exp/default/logs", params={"order": "earliest", "n": 3})
        logs = resp.json()["logs"]
        assert len(logs) == 3
        assert logs[0]["id"] < logs[1]["id"] < logs[2]["id"]

    def test_log_limit_n(self, admin_client):
        _register_student(admin_client)
        for i in range(10):
            _call_func(admin_client, args=[i])
        resp = admin_client.get("/exp/default/logs", params={"n": 3})
        assert len(resp.json()["logs"]) == 3

    def test_log_cursor_pagination(self, admin_client):
        _register_student(admin_client)
        for i in range(5):
            _call_func(admin_client, args=[i])

        page1 = admin_client.get("/exp/default/logs", params={"n": 2, "order": "latest"}).json()["logs"]
        assert len(page1) == 2

        page2 = admin_client.get("/exp/default/logs", params={
            "n": 2, "order": "latest", "after_id": page1[-1]["id"],
        }).json()["logs"]
        assert len(page2) == 2
        assert all(l["id"] < page1[-1]["id"] for l in page2)

    def test_log_combined_filters(self, admin_client):
        _register_student(admin_client, sid="s001", name="Alice")
        _register_student(admin_client, sid="s002", name="Bob")
        _call_func(admin_client, sid="s001", func="square", args=[1], trial="a")
        _call_func(admin_client, sid="s001", func="add", args=[1, 2], trial="a")
        _call_func(admin_client, sid="s002", func="square", args=[3], trial="a")
        _call_func(admin_client, sid="s001", func="square", args=[4], trial="b")

        resp = admin_client.get("/exp/default/logs", params={
            "student_id": "s001", "func_name": "square", "trial_name": "a",
        })
        logs = resp.json()["logs"]
        assert len(logs) == 1
        assert logs[0]["args"] == [1]

    def test_log_options(self, admin_client):
        _register_student(admin_client)
        resp = admin_client.get("/exp/default/log-options")
        assert resp.status_code == 200
        data = resp.json()
        assert "students" in data
        assert "trials" in data
        assert "s001" in data["students"]

    def test_log_options_with_trials(self, admin_client):
        _register_student(admin_client)
        _call_func(admin_client, args=[1], trial="run-1")
        _call_func(admin_client, args=[2], trial="run-2")
        data = admin_client.get("/exp/default/log-options").json()
        assert set(data["trials"]) == {"run-1", "run-2"}

    def test_log_options_empty(self, client):
        data = client.get("/exp/default/log-options").json()
        assert data["students"] == []
        assert data["trials"] == []


# ── Reload Functions ──


class TestReloadFunctions:
    def test_reload(self, admin_client):
        resp = admin_client.post("/exp/default/admin/reload")
        assert resp.status_code == 200
        assert resp.json()["functions_loaded"] >= 1

    def test_reload_unknown_experiment(self, admin_client):
        resp = admin_client.post("/exp/nonexistent/admin/reload")
        assert resp.status_code == 404


# ── Decorators via API ──


class TestDecoratorsViaAPI:
    """Test @nolog and @noregcheck behavior through the full API stack."""

    def test_noregcheck_allows_unregistered(self, client):
        """echo() has @noregcheck — any student_id should work without registration."""
        resp = _call_func(client, func="echo", sid="anon_user", args=["hello"])
        assert resp.status_code == 200
        assert resp.json()["result"] == "hello"

    def test_noregcheck_still_logged(self, client):
        """@noregcheck functions should still appear in logs."""
        _call_func(client, func="echo", sid="anon_user", args=[42])
        logs = client.get("/exp/default/logs").json()["logs"]
        echo_logs = [l for l in logs if l["func_name"] == "echo"]
        assert len(echo_logs) == 1
        assert echo_logs[0]["student_id"] == "anon_user"
        assert echo_logs[0]["args"] == [42]

    def test_noregcheck_ping(self, client):
        resp = _call_func(client, func="ping", sid="anyone", args=[])
        assert resp.status_code == 200
        assert resp.json()["result"] == "pong"

    def test_nolog_not_logged(self, admin_client):
        """fast_step() has @nolog — should execute but NOT create a log entry."""
        _register_student(admin_client)
        resp = _call_func(admin_client, func="fast_step", sid="s001", args=[5])
        assert resp.status_code == 200
        assert resp.json()["result"] == 10

        logs = admin_client.get("/exp/default/logs").json()["logs"]
        fast_logs = [l for l in logs if l["func_name"] == "fast_step"]
        assert len(fast_logs) == 0

    def test_nolog_still_requires_registration(self, client):
        """@nolog does NOT skip registration check."""
        resp = _call_func(client, func="fast_step", sid="nobody", args=[1])
        assert resp.status_code == 403

    def test_logged_function_next_to_nolog(self, admin_client):
        """logged_reset() is in same file as @nolog fast_step() — should be logged."""
        _register_student(admin_client)
        resp = _call_func(admin_client, func="logged_reset", sid="s001", args=[])
        assert resp.status_code == 200
        assert resp.json()["result"] == "reset"

        logs = admin_client.get("/exp/default/logs").json()["logs"]
        reset_logs = [l for l in logs if l["func_name"] == "logged_reset"]
        assert len(reset_logs) == 1

    def test_mixed_calls_selective_logging(self, admin_client):
        """Mix @nolog and normal calls — only normal calls appear in logs."""
        _register_student(admin_client)
        _call_func(admin_client, func="square", sid="s001", args=[3])
        _call_func(admin_client, func="fast_step", sid="s001", args=[10])
        _call_func(admin_client, func="cubic", sid="s001", args=[2])
        _call_func(admin_client, func="fast_step", sid="s001", args=[20])

        logs = admin_client.get("/exp/default/logs").json()["logs"]
        func_names = [l["func_name"] for l in logs]
        assert "square" in func_names
        assert "cubic" in func_names
        assert "fast_step" not in func_names
        assert len(logs) == 2

    def test_all_functions_discoverable(self, client):
        """All functions (including decorated) should appear in /functions."""
        funcs = client.get("/exp/default/functions").json()
        assert "square" in funcs
        assert "echo" in funcs
        assert "ping" in funcs
        assert "fast_step" in funcs
        assert "logged_reset" in funcs
        assert "wipe_data" in funcs

    def test_adminonly_blocked_without_admin(self, client):
        """@adminonly function returns 403 for non-admin callers."""
        resp = _call_func(client, func="wipe_data", sid="anon_user")
        assert resp.status_code == 403
        assert "Admin access required" in resp.json()["detail"]

    def test_adminonly_blocked_for_registered_student(self, admin_client):
        """@adminonly function returns 403 even for registered students without admin session."""
        _register_student(admin_client)
        # Create a non-admin client by using a fresh TestClient
        from fastapi.testclient import TestClient
        app = admin_client.app
        with TestClient(app) as non_admin:
            resp = _call_func(non_admin, func="wipe_data", sid="s001")
            assert resp.status_code == 403

    def test_adminonly_allowed_for_admin(self, admin_client):
        """@adminonly function executes successfully for admin sessions."""
        _register_student(admin_client)
        resp = _call_func(admin_client, func="wipe_data", sid="s001")
        assert resp.status_code == 200
        assert resp.json()["result"] == "wiped"

    def test_adminonly_function_metadata(self, client):
        """@adminonly flag is exposed in function metadata."""
        funcs = client.get("/exp/default/functions").json()
        assert funcs["wipe_data"]["adminonly"] is True
        assert funcs["square"]["adminonly"] is False


# ── Export Logs ──


class TestExportLogs:
    def test_export_empty(self, admin_client):
        resp = admin_client.get("/exp/default/admin/export-logs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["count"] == 0
        assert data["logs"] == []

    def test_export_with_logs(self, admin_client):
        _register_student(admin_client)
        _call_func(admin_client, func="square", sid="s001", args=[3])
        _call_func(admin_client, func="add", sid="s001", args=[1, 2])
        resp = admin_client.get("/exp/default/admin/export-logs")
        data = resp.json()
        assert data["count"] == 2
        assert len(data["logs"]) == 2

    def test_export_requires_auth(self, client):
        resp = client.get("/exp/default/admin/export-logs")
        assert resp.status_code == 401

    def test_export_invalid_format(self, admin_client):
        resp = admin_client.get("/exp/default/admin/export-logs?format=xml")
        assert resp.status_code == 400


# ── Delete Log (admin only) ──


class TestDeleteLog:
    def test_delete_log_requires_auth(self, client):
        resp = client.post("/exp/default/admin/delete-log", json={"log_id": 1})
        assert resp.status_code == 401

    def test_delete_log_success(self, admin_client):
        _register_student(admin_client)
        _call_func(admin_client, func="square", sid="s001", args=[5])
        logs_resp = admin_client.get("/exp/default/logs?n=10")
        logs = logs_resp.json()["logs"]
        assert len(logs) == 1
        log_id = logs[0]["id"]
        resp = admin_client.post(
            "/exp/default/admin/delete-log",
            json={"log_id": log_id},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "log_id": log_id}
        logs_resp2 = admin_client.get("/exp/default/logs?n=10")
        assert len(logs_resp2.json()["logs"]) == 0

    def test_delete_log_nonexistent(self, admin_client):
        resp = admin_client.post(
            "/exp/default/admin/delete-log",
            json={"log_id": 99999},
        )
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()


# ── Delete Logs (bulk, admin only) ──


class TestDeleteLogs:
    def test_requires_auth(self, client):
        resp = client.post("/exp/default/admin/delete-logs", json={"student_id": "s001"})
        assert resp.status_code == 401

    def test_requires_filter(self, admin_client):
        resp = admin_client.post("/exp/default/admin/delete-logs", json={})
        assert resp.status_code == 400
        assert "at least one" in resp.json()["detail"].lower()

    def test_delete_by_student(self, admin_client):
        _register_student(admin_client)
        _call_func(admin_client, sid="s001", args=[5])
        _call_func(admin_client, sid="s001", args=[6])
        resp = admin_client.post(
            "/exp/default/admin/delete-logs",
            json={"student_id": "s001"},
        )
        assert resp.status_code == 200
        assert resp.json()["deleted"] == 2
        # Student still exists
        assert admin_client.get(
            "/exp/default/is-registered", params={"student_id": "s001"}
        ).json()["registered"] is True
        # Logs gone
        assert len(admin_client.get("/exp/default/logs?n=10").json()["logs"]) == 0

    def test_delete_by_trial(self, admin_client):
        _register_student(admin_client)
        _call_func(admin_client, sid="s001", args=[5], trial="run1")
        _call_func(admin_client, sid="s001", args=[6], trial="run2")
        resp = admin_client.post(
            "/exp/default/admin/delete-logs",
            json={"trial": "run1"},
        )
        assert resp.status_code == 200
        assert resp.json()["deleted"] == 1
        remaining = admin_client.get("/exp/default/logs?n=10").json()["logs"]
        assert len(remaining) == 1
        assert remaining[0]["trial"] == "run2"


# ── Change Password ──


class TestChangePassword:
    def test_change_password_success(self, admin_client):
        resp = admin_client.post(
            "/api/admin/change-password",
            json={"current_password": "testpass", "new_password": "newpass123"},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        # Verify new password works for login
        admin_client.post("/logout")
        resp = admin_client.post("/login", json={"password": "newpass123"})
        assert resp.status_code == 200

    def test_change_password_wrong_current(self, admin_client):
        resp = admin_client.post(
            "/api/admin/change-password",
            json={"current_password": "wrong", "new_password": "newpass"},
        )
        assert resp.status_code == 401

    def test_change_password_empty_new(self, admin_client):
        resp = admin_client.post(
            "/api/admin/change-password",
            json={"current_password": "testpass", "new_password": "  "},
        )
        assert resp.status_code == 400

    def test_change_password_requires_auth(self, client):
        resp = client.post(
            "/api/admin/change-password",
            json={"current_password": "testpass", "new_password": "newpass"},
        )
        assert resp.status_code == 401
