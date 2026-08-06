# Agent Instructions

## Read first

Before changing code, read:

1. `README.md`
2. `PROJECT.md`
3. `TASK.md`
4. `docs/architecture.md`
5. relevant ADRs

## Operating rules

- Make the smallest change that satisfies the approved task.
- Do not redesign the product during implementation.
- Do not read or commit `.env`, credentials, private keys, tokens, or ignored private notes.
- Do not run `git push`, deploy, publish, or modify production data without explicit human approval.
- Do not suppress a failing test merely to make CI green.
- Do not claim a command passed unless its real exit code and output were observed.
- Preserve public development evidence: decision, diff, commands, tests, and result.
- Use structured models for cross-role handoffs.
- Add or update tests for behavior changes.
- Keep generated run artifacts under `.soloscale/`, which is ignored by default.

## Commands

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
ruff check .
mypy src tests
pytest -q
python -m build
```

## Definition of done

- behavior matches the approved issue or Execution Packet
- targeted and full relevant tests pass
- lint and type checks pass
- no unrelated changes
- development record updated
- remaining risks stated honestly
