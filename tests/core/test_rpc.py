"""Tests for leap.core.rpc — decorators, validation, and RPC execution."""

from __future__ import annotations

from pathlib import Path

import pytest

from leap.core import rpc, storage
from leap.core.experiment import ExperimentInfo


@pytest.fixture
def exp_with_session(tmp_root: Path):
    exp = ExperimentInfo("default", tmp_root / "experiments" / "default")
    session = storage.get_session("default", exp.db_path)
    yield exp, session
    session.close()
    storage.close_all_engines()


@pytest.fixture
def open_exp_with_session(tmp_path: Path):
    """Experiment with require_registration=False."""
    exp_dir = tmp_path / "experiments" / "open"
    funcs_dir = exp_dir / "funcs"
    funcs_dir.mkdir(parents=True)
    (exp_dir / "db").mkdir()
    (exp_dir / "README.md").write_text(
        "---\nname: open\nrequire_registration: false\n---\n"
    )
    (funcs_dir / "funcs.py").write_text(
        "def echo(x): return x\n"
        "def greet(name): return f'hello {name}'\n"
    )
    exp = ExperimentInfo("open", exp_dir)
    session = storage.get_session("open", exp.db_path)
    yield exp, session
    session.close()
    storage.close_all_engines()


# ── Decorators ──


class TestDecorators:
    def test_nolog_flag(self):
        @rpc.nolog
        def fast(): pass
        assert rpc._has_flag(fast, "_leap_nolog") is True

    def test_noregcheck_flag(self):
        @rpc.noregcheck
        def open_fn(): pass
        assert rpc._has_flag(open_fn, "_leap_noregcheck") is True

    def test_adminonly_flag(self):
        @rpc.adminonly
        def restricted(): pass
        assert rpc._has_flag(restricted, "_leap_adminonly") is True

    def test_no_flag_by_default(self):
        def normal(): pass
        assert rpc._has_flag(normal, "_leap_nolog") is False
        assert rpc._has_flag(normal, "_leap_noregcheck") is False
        assert rpc._has_flag(normal, "_leap_adminonly") is False

    def test_combined_decorators(self):
        @rpc.nolog
        @rpc.noregcheck
        def both(): pass
        assert rpc._has_flag(both, "_leap_nolog") is True
        assert rpc._has_flag(both, "_leap_noregcheck") is True

    def test_decorators_preserve_function(self):
        @rpc.nolog
        def calc(x): return x * 2
        assert calc(5) == 10

        @rpc.noregcheck
        def calc2(x): return x + 1
        assert calc2(5) == 6

    def test_decorator_order_independent(self):
        @rpc.noregcheck
        @rpc.nolog
        def order1(): pass

        @rpc.nolog
        @rpc.noregcheck
        def order2(): pass

        assert rpc._has_flag(order1, "_leap_nolog") is True
        assert rpc._has_flag(order1, "_leap_noregcheck") is True
        assert rpc._has_flag(order2, "_leap_nolog") is True
        assert rpc._has_flag(order2, "_leap_noregcheck") is True


# ── Student ID Validation ──


class TestStudentIdValidation:
    @pytest.mark.parametrize("sid", [
        "s001", "alice-bob", "test_123", "A",
        pytest.param("a" * 255, id="max-255-chars"),
        "ABC-123", "student_01", "x",
    ])
    def test_valid(self, sid):
        assert rpc.validate_student_id(sid) is True

    @pytest.mark.parametrize("sid", [
        "", "a b",
        pytest.param("x" * 256, id="over-255-chars"),
        "a/b", "a\\b", "name@email", "hello!", "a\tb", "a\nb",
    ])
    def test_invalid(self, sid):
        assert rpc.validate_student_id(sid) is False


# ── Execute RPC (require_registration=True) ──


class TestExecuteRPC:
    def test_basic_call(self, exp_with_session):
        exp, session = exp_with_session
        storage.add_student(session, "s001", "Alice")
        result = rpc.execute_rpc(
            exp, session, func_name="square", args=[7], student_id="s001"
        )
        assert result == 49

    def test_call_logged(self, exp_with_session):
        exp, session = exp_with_session
        storage.add_student(session, "s001", "Alice")
        rpc.execute_rpc(exp, session, func_name="square", args=[5], student_id="s001")
        logs = storage.query_logs(session)
        assert len(logs) == 1
        assert logs[0]["func_name"] == "square"
        assert logs[0]["args"] == [5]
        assert logs[0]["result"] == 25

    def test_log_experiment_field(self, exp_with_session):
        exp, session = exp_with_session
        storage.add_student(session, "s001", "Alice")
        rpc.execute_rpc(exp, session, func_name="square", args=[2], student_id="s001")
        logs = storage.query_logs(session)
        assert logs[0]["experiment"] == "default"

    def test_log_student_id_field(self, exp_with_session):
        exp, session = exp_with_session
        storage.add_student(session, "s001", "Alice")
        rpc.execute_rpc(exp, session, func_name="square", args=[2], student_id="s001")
        logs = storage.query_logs(session)
        assert logs[0]["student_id"] == "s001"

    def test_unknown_function(self, exp_with_session):
        exp, session = exp_with_session
        storage.add_student(session, "s001", "Alice")
        with pytest.raises(ValueError, match="Unknown function"):
            rpc.execute_rpc(exp, session, func_name="nope", args=[], student_id="s001")

    def test_unregistered_student(self, exp_with_session):
        exp, session = exp_with_session
        with pytest.raises(PermissionError, match="not registered"):
            rpc.execute_rpc(exp, session, func_name="square", args=[1], student_id="nobody")

    def test_invalid_student_id_format(self, exp_with_session):
        exp, session = exp_with_session
        with pytest.raises(ValueError, match="Invalid student_id"):
            rpc.execute_rpc(exp, session, func_name="square", args=[1], student_id="bad id!")

    def test_nolog_skips_logging(self, exp_with_session):
        exp, session = exp_with_session
        storage.add_student(session, "s001", "Alice")

        @rpc.nolog
        def fast(x): return x * 2
        exp.functions["fast"] = fast

        result = rpc.execute_rpc(exp, session, func_name="fast", args=[10], student_id="s001")
        assert result == 20
        assert len(storage.query_logs(session, func_name="fast")) == 0

    def test_noregcheck_bypasses_registration(self, exp_with_session):
        exp, session = exp_with_session

        @rpc.noregcheck
        def open_fn(x): return x
        exp.functions["open_fn"] = open_fn

        result = rpc.execute_rpc(exp, session, func_name="open_fn", args=[42], student_id="anyone")
        assert result == 42

    def test_combined_nolog_noregcheck(self, exp_with_session):
        exp, session = exp_with_session

        @rpc.nolog
        @rpc.noregcheck
        def stealth(x): return x
        exp.functions["stealth"] = stealth

        result = rpc.execute_rpc(exp, session, func_name="stealth", args=[99], student_id="anon")
        assert result == 99
        assert len(storage.query_logs(session, func_name="stealth")) == 0

    def test_function_error_logged(self, exp_with_session):
        exp, session = exp_with_session
        storage.add_student(session, "s001", "Alice")

        def bad(): raise ZeroDivisionError("oops")
        exp.functions["bad"] = bad

        with pytest.raises(RuntimeError, match="oops"):
            rpc.execute_rpc(exp, session, func_name="bad", args=[], student_id="s001")

        logs = storage.query_logs(session, func_name="bad")
        assert len(logs) == 1
        assert "ZeroDivisionError" in logs[0]["error"]
        assert logs[0]["result"] is None

    def test_function_error_nolog_not_logged(self, exp_with_session):
        exp, session = exp_with_session
        storage.add_student(session, "s001", "Alice")

        @rpc.nolog
        def bad_nolog(): raise ValueError("skip me")
        exp.functions["bad_nolog"] = bad_nolog

        with pytest.raises(RuntimeError):
            rpc.execute_rpc(exp, session, func_name="bad_nolog", args=[], student_id="s001")
        assert len(storage.query_logs(session, func_name="bad_nolog")) == 0

    def test_trial_passed_to_log(self, exp_with_session):
        exp, session = exp_with_session
        storage.add_student(session, "s001", "Alice")
        rpc.execute_rpc(
            exp, session,
            func_name="square", args=[3], student_id="s001", trial="run-1",
        )
        logs = storage.query_logs(session, trial="run-1")
        assert len(logs) == 1

    def test_multiple_calls_create_multiple_logs(self, exp_with_session):
        exp, session = exp_with_session
        storage.add_student(session, "s001", "Alice")
        for i in range(5):
            rpc.execute_rpc(exp, session, func_name="square", args=[i], student_id="s001")
        logs = storage.query_logs(session)
        assert len(logs) == 5

    def test_function_returning_none(self, exp_with_session):
        exp, session = exp_with_session
        storage.add_student(session, "s001", "Alice")

        def returns_none(): return None
        exp.functions["returns_none"] = returns_none

        result = rpc.execute_rpc(exp, session, func_name="returns_none", args=[], student_id="s001")
        assert result is None

    def test_function_returning_complex_data(self, exp_with_session):
        exp, session = exp_with_session
        storage.add_student(session, "s001", "Alice")

        def complex_fn():
            return {"matrix": [[1, 2], [3, 4]], "eigenvalues": [5.37, -0.37]}
        exp.functions["complex_fn"] = complex_fn

        result = rpc.execute_rpc(exp, session, func_name="complex_fn", args=[], student_id="s001")
        assert result["matrix"] == [[1, 2], [3, 4]]

    def test_function_with_add(self, exp_with_session):
        """Test the 'add' function from the default experiment."""
        exp, session = exp_with_session
        storage.add_student(session, "s001", "Alice")
        result = rpc.execute_rpc(exp, session, func_name="add", args=[3, 4], student_id="s001")
        assert result == 7


# ── Execute RPC (require_registration=False) ──


class TestExecuteRPCOpenExperiment:
    def test_any_student_accepted(self, open_exp_with_session):
        exp, session = open_exp_with_session
        result = rpc.execute_rpc(
            exp, session, func_name="echo", args=["hello"], student_id="anyone"
        )
        assert result == "hello"

    def test_still_logged(self, open_exp_with_session):
        exp, session = open_exp_with_session
        rpc.execute_rpc(
            exp, session, func_name="echo", args=[42], student_id="anon"
        )
        logs = storage.query_logs(session)
        assert len(logs) == 1
        assert logs[0]["student_id"] == "anon"

    def test_multiple_students_no_registration(self, open_exp_with_session):
        exp, session = open_exp_with_session
        for sid in ["alice", "bob", "charlie"]:
            rpc.execute_rpc(exp, session, func_name="echo", args=[sid], student_id=sid)
        logs = storage.query_logs(session)
        assert len(logs) == 3


# ── Rate Limiting ──


class TestRateLimiting:
    @pytest.fixture(autouse=True)
    def enable_rate_limiting(self, monkeypatch):
        """Enable rate limiting for these tests (conftest disables it globally)."""
        monkeypatch.setenv("LEAP_RATE_LIMIT", "1")
        # Clear the global rate windows between tests
        rpc._rate_windows.clear()

    def test_ratelimit_decorator_sets_attribute(self):
        @rpc.ratelimit("10/minute")
        def fn(): pass
        assert fn._leap_ratelimit == "10/minute"

    def test_ratelimit_false_disables(self):
        @rpc.ratelimit(False)
        def fn(): pass
        assert fn._leap_ratelimit is False

    def test_ratelimit_preserves_function(self):
        @rpc.ratelimit("5/second")
        def calc(x): return x * 3
        assert calc(4) == 12

    def test_rate_limit_enforced(self, open_exp_with_session):
        exp, session = open_exp_with_session

        @rpc.ratelimit("2/minute")
        def limited(x): return x
        exp.functions["limited"] = limited

        # First 2 calls succeed
        assert rpc.execute_rpc(exp, session, func_name="limited", args=[1], student_id="s1") == 1
        assert rpc.execute_rpc(exp, session, func_name="limited", args=[2], student_id="s1") == 2

        # 3rd call exceeds limit
        with pytest.raises(rpc.RateLimitError, match="Rate limit exceeded"):
            rpc.execute_rpc(exp, session, func_name="limited", args=[3], student_id="s1")

    def test_ratelimit_false_unlimited(self, open_exp_with_session):
        exp, session = open_exp_with_session

        @rpc.ratelimit(False)
        def unlimited(x): return x
        exp.functions["unlimited"] = unlimited

        # Many calls all succeed
        for i in range(50):
            assert rpc.execute_rpc(exp, session, func_name="unlimited", args=[i], student_id="s1") == i

    def test_different_students_independent(self, open_exp_with_session):
        exp, session = open_exp_with_session

        @rpc.ratelimit("2/minute")
        def limited(x): return x
        exp.functions["limited"] = limited

        # Student A uses up their limit
        rpc.execute_rpc(exp, session, func_name="limited", args=[1], student_id="alice")
        rpc.execute_rpc(exp, session, func_name="limited", args=[2], student_id="alice")
        with pytest.raises(rpc.RateLimitError):
            rpc.execute_rpc(exp, session, func_name="limited", args=[3], student_id="alice")

        # Student B is independent — still has quota
        assert rpc.execute_rpc(exp, session, func_name="limited", args=[1], student_id="bob") == 1
        assert rpc.execute_rpc(exp, session, func_name="limited", args=[2], student_id="bob") == 2

    def test_default_rate_limit_applied(self, open_exp_with_session):
        """Functions without @ratelimit get the default limit."""
        exp, session = open_exp_with_session
        # echo has no @ratelimit decorator — should use default (120/minute)
        func = exp.functions["echo"]
        assert not hasattr(func, "_leap_ratelimit")
        # Should succeed (well under 120/minute)
        for i in range(5):
            rpc.execute_rpc(exp, session, func_name="echo", args=[i], student_id="s1")

    def test_parse_limit_various_periods(self):
        assert rpc._parse_limit("10/second") == (10, 1)
        assert rpc._parse_limit("60/minute") == (60, 60)
        assert rpc._parse_limit("100/hour") == (100, 3600)
        assert rpc._parse_limit("1000/day") == (1000, 86400)
