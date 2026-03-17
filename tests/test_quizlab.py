"""Tests for the quizlab experiment functions (quiz parsing, grading, scores)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


@pytest.fixture
def quizlab_env(tmp_path):
    """Set up a minimal quizlab-like environment and import quiz module."""
    quiz_dir = tmp_path / "quiz"
    quiz_dir.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    funcs_dir = tmp_path / "funcs"
    funcs_dir.mkdir()

    # Write a sample quiz
    (quiz_dir / "sample.md").write_text(
        "---\n"
        "title: Sample Quiz\n"
        "allow_resubmit: false\n"
        "---\n\n"
        "## Question 1: q1 [2]\n\n"
        "What is 1+1?\n\n"
        "- ( ) 1\n"
        "- (x) 2\n"
        "- ( ) 3\n\n"
        "> Simple addition.\n\n"
        "---\n\n"
        "## Question 2: q2 [3]\n\n"
        "Select all even numbers:\n\n"
        "- [x] 2\n"
        "- [ ] 3\n"
        "- [x] 4\n\n"
        "---\n\n"
        "## Question 3: q3 [5]\n\n"
        "Compute 2+2.\n\n"
        "= 4\n\n"
        "> 2+2=4.\n"
    )

    # Patch the quiz module's directory helpers to use tmp_path
    quiz_module_path = Path(__file__).resolve().parent.parent / "experiments" / "quizlab" / "funcs"
    sys.path.insert(0, str(quiz_module_path))
    import quiz as quiz_mod
    sys.path.pop(0)

    original_quiz_dir = quiz_mod._quiz_dir
    original_data_dir = quiz_mod._data_dir
    quiz_mod._quiz_dir = lambda: quiz_dir
    quiz_mod._data_dir = lambda: data_dir

    yield quiz_mod, quiz_dir, data_dir

    # Restore
    quiz_mod._quiz_dir = original_quiz_dir
    quiz_mod._data_dir = original_data_dir


class TestGetAllScores:
    def test_no_submissions(self, quizlab_env):
        quiz_mod, _, _ = quizlab_env
        result = quiz_mod.get_all_scores("sample.md")
        assert result["quiz_title"] == "Sample Quiz"
        assert len(result["questions"]) == 3
        assert result["questions"][0] == {"qid": "q1", "points": 2}
        assert result["questions"][1] == {"qid": "q2", "points": 3}
        assert result["questions"][2] == {"qid": "q3", "points": 5}
        assert result["students"] == []

    def test_single_student_all_correct(self, quizlab_env):
        quiz_mod, _, data_dir = quizlab_env
        records = [
            {"student_id": "s001", "quiz_file": "sample.md", "question_id": "q1", "answer": 1, "result": {"correct": True, "expected": 1, "points": 2}},
            {"student_id": "s001", "quiz_file": "sample.md", "question_id": "q2", "answer": [0, 2], "result": {"correct": True, "expected": [0, 2], "points": 3}},
            {"student_id": "s001", "quiz_file": "sample.md", "question_id": "q3", "answer": 4.0, "result": {"correct": True, "expected": 4.0, "points": 5}},
        ]
        (data_dir / "s001.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n")

        result = quiz_mod.get_all_scores("sample.md")
        assert len(result["students"]) == 1
        student = result["students"][0]
        assert student["student_id"] == "s001"
        assert student["total_earned"] == 10
        assert student["total_possible"] == 10
        assert student["scores"]["q1"]["correct"] is True
        assert student["scores"]["q1"]["points_earned"] == 2

    def test_partial_submission(self, quizlab_env):
        quiz_mod, _, data_dir = quizlab_env
        records = [
            {"student_id": "s002", "quiz_file": "sample.md", "question_id": "q1", "answer": 0, "result": {"correct": False, "expected": 1, "points": 2}},
        ]
        (data_dir / "s002.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n")

        result = quiz_mod.get_all_scores("sample.md")
        student = result["students"][0]
        assert student["total_earned"] == 0
        assert student["total_possible"] == 10
        assert student["scores"]["q1"]["correct"] is False
        assert student["scores"]["q1"]["points_earned"] == 0
        assert "q2" not in student["scores"]
        assert "q3" not in student["scores"]

    def test_latest_submission_wins(self, quizlab_env):
        quiz_mod, _, data_dir = quizlab_env
        records = [
            {"student_id": "s003", "quiz_file": "sample.md", "question_id": "q1", "answer": 0, "result": {"correct": False, "expected": 1, "points": 2}},
            {"student_id": "s003", "quiz_file": "sample.md", "question_id": "q1", "answer": 1, "result": {"correct": True, "expected": 1, "points": 2}},
        ]
        (data_dir / "s003.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n")

        result = quiz_mod.get_all_scores("sample.md")
        student = result["students"][0]
        assert student["scores"]["q1"]["correct"] is True
        assert student["total_earned"] == 2

    def test_multiple_students_sorted(self, quizlab_env):
        quiz_mod, _, data_dir = quizlab_env
        for sid in ["s005", "s001", "s003"]:
            records = [
                {"student_id": sid, "quiz_file": "sample.md", "question_id": "q1", "answer": 1, "result": {"correct": True, "expected": 1, "points": 2}},
            ]
            (data_dir / f"{sid}.jsonl").write_text(json.dumps(records[0]) + "\n")

        result = quiz_mod.get_all_scores("sample.md")
        ids = [s["student_id"] for s in result["students"]]
        assert ids == ["s001", "s003", "s005"]

    def test_filters_by_quiz_file(self, quizlab_env):
        quiz_mod, _, data_dir = quizlab_env
        records = [
            {"student_id": "s001", "quiz_file": "other.md", "question_id": "q1", "answer": 1, "result": {"correct": True, "points": 2}},
            {"student_id": "s001", "quiz_file": "sample.md", "question_id": "q1", "answer": 1, "result": {"correct": True, "points": 2}},
        ]
        (data_dir / "s001.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n")

        result = quiz_mod.get_all_scores("sample.md")
        assert len(result["students"]) == 1
        assert "q1" in result["students"][0]["scores"]

    def test_invalid_quiz_file(self, quizlab_env):
        quiz_mod, _, _ = quizlab_env
        with pytest.raises(ValueError, match="Quiz not found"):
            quiz_mod.get_all_scores("nonexistent.md")

    def test_skips_students_with_no_matching_submissions(self, quizlab_env):
        quiz_mod, _, data_dir = quizlab_env
        records = [
            {"student_id": "s001", "quiz_file": "other.md", "question_id": "q1", "answer": 1, "result": {"correct": True, "points": 2}},
        ]
        (data_dir / "s001.jsonl").write_text(json.dumps(records[0]) + "\n")

        result = quiz_mod.get_all_scores("sample.md")
        assert result["students"] == []


class TestQuizParsing:
    def test_list_quizzes(self, quizlab_env):
        quiz_mod, _, _ = quizlab_env
        quizzes = quiz_mod.list_quizzes()
        assert len(quizzes) == 1
        assert quizzes[0]["file"] == "sample.md"
        assert quizzes[0]["title"] == "Sample Quiz"

    def test_get_quiz_strips_answers(self, quizlab_env):
        quiz_mod, _, _ = quizlab_env
        result = quiz_mod.get_quiz("sample.md")
        assert "(x)" not in result["body"]
        assert "[x]" not in result["body"]
        assert "= 4" not in result["body"]
        assert "???" in result["body"]

    def test_grade_correct_radio(self, quizlab_env):
        quiz_mod, _, _ = quizlab_env
        result = quiz_mod.grade("s001", "sample.md", "q1", 1)
        assert result["correct"] is True
        assert result["points"] == 2

    def test_grade_incorrect_radio(self, quizlab_env):
        quiz_mod, _, _ = quizlab_env
        result = quiz_mod.grade("s001", "sample.md", "q1", 0)
        assert result["correct"] is False

    def test_grade_numeric(self, quizlab_env):
        quiz_mod, _, _ = quizlab_env
        result = quiz_mod.grade("s001", "sample.md", "q3", 4.0)
        assert result["correct"] is True
        assert result["points"] == 5

    def test_grade_checkbox(self, quizlab_env):
        quiz_mod, _, _ = quizlab_env
        result = quiz_mod.grade("s001", "sample.md", "q2", [0, 2])
        assert result["correct"] is True
        assert result["points"] == 3
