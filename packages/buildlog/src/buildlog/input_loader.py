"""Load and validate iteration JSON files."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from buildlog.exceptions import InputFileError
from buildlog.models import Iteration


def load_iteration(path: Path) -> Iteration:
    """Load one iteration JSON file and return a validated model."""
    if not path.exists():
        raise InputFileError(f'input file "{path}" does not exist')
    if not path.is_file():
        raise InputFileError(f'input path "{path}" is not a file')

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InputFileError(f"invalid JSON: {exc.msg}") from exc
    except OSError as exc:
        raise InputFileError(f"could not read input file: {exc}") from exc

    try:
        return Iteration.model_validate(data)
    except ValidationError as exc:
        raise InputFileError(str(exc)) from exc
