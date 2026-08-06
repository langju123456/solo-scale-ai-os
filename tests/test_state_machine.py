import pytest

from soloscale.models import TaskStatus
from soloscale.state_machine import InvalidTransition, validate_transition


def test_valid_transition() -> None:
    result = validate_transition(TaskStatus.NEW, TaskStatus.TRIAGED)
    assert result.target is TaskStatus.TRIAGED


def test_invalid_transition() -> None:
    with pytest.raises(InvalidTransition):
        validate_transition(TaskStatus.NEW, TaskStatus.CLOSED)
