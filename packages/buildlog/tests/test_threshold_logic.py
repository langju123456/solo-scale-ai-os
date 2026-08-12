"""Tests for evaluator threshold logic."""

from __future__ import annotations

from pathlib import Path

from buildlog.config import load_settings
from buildlog.evaluator import passes_thresholds
from buildlog.models import Evaluation


def test_evaluation_threshold_pass() -> None:
    settings = load_settings(Path.cwd())
    evaluation = Evaluation(
        technical_accuracy=8,
        specificity=7,
        readability=7,
        reader_value=7,
        evidence_coverage=7,
    )

    assert passes_thresholds(evaluation, settings)


def test_evaluation_threshold_fail() -> None:
    settings = load_settings(Path.cwd())
    evaluation = Evaluation(
        technical_accuracy=7,
        specificity=10,
        readability=10,
        reader_value=10,
        evidence_coverage=10,
    )

    assert not passes_thresholds(evaluation, settings)


def test_hard_failure_fails_thresholds() -> None:
    settings = load_settings(Path.cwd())
    evaluation = Evaluation(
        technical_accuracy=10,
        specificity=10,
        readability=10,
        reader_value=10,
        evidence_coverage=10,
        hard_failure=True,
    )

    assert not passes_thresholds(evaluation, settings)
