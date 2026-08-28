# SoloScale Current Handoff

Updated: 2026-08-29

Canonical worktree: `/Users/ju.l/Documents/AI TEAM/AI TEAM WORKTREES/solo-scale-ai-os-macos-desktop-app`

## CURRENT BRANCH / HEAD

- branch: `codex/macos-desktop-app`
- HEAD: `4ae6b80` (pushed to `origin/codex/macos-desktop-app`)

## COMPLETED CHECKPOINTS

- `b4ae09d` — Module A: creator production and publishing consolidation.
- `53db791` — Module B: work source control plane + advanced diagnostics.
- `4ae6b80` — Module C: learning mastery separated from claim eligibility; project
  binding + seed-case fixture decoupling; freshness preflight before case build.

## CURRENT DIRTY WORK (ownership by future slice)

- Resume atomic-facts/parser thread: `src/soloscale/resume_docx.py`,
  `resume_evidence_pack.py`, `resume_gateway_boundary.py`, `resume_models.py`,
  `resume_template_intake.py`, `tests/test_resume_docx.py`, Resume hunks in
  `src/soloscale/local_ui.py` and `tests/test_local_ui.py`.
- Web/Vercel thread (disposition decision pending): `.vercelignore`, `api/`,
  `requirements.txt`, `src/soloscale/app_web.py`, `src/soloscale/resume_web.py`,
  `tests/test_app_web.py`, `tests/test_resume_input_quality.py`,
  `tests/test_resume_web.py`, `vercel.json`, `docs/guides/`, `docs/operations/`.
- Other/unknown ownership: `PROJECT.md`, `README.md`, `ROADMAP.md`, `TASK.md`,
  `docs/architecture.md`, `pyproject.toml`,
  `desktop/macos/Sources/SoloScaleDesktop/main.swift`.

## KNOWN ISSUES

- `tests/test_github_connect.py::test_github_read_only_selection_evidence_and_resume_boundary`
  fails at the approved checkpoints (resume pipeline produces 0 desktop facts) —
  assigned to the Resume atomic-facts thread.
- mypy on the dirty tree reports 39 errors, all inside other-thread hunks
  (Creator/Resume/YouTube); Modules A/B/C added zero.
- Desktop toolchain: pinned Command Line Tools fallback is provisional; full Xcode
  is not installed.
- The installed `/Applications/SoloScale AI OS.app` may be behind source; verify
  bundle identity before claiming installed behavior.

## NEXT CANDIDATE SLICES

1. Resume atomic-facts reconciliation (including the GitHub/resume boundary test
   failure) — highest priority.
2. Web/Vercel disposition decision (keep as a separate branch/PR vs defer per the
   Local-MVP policy).
3. YouTube OAuth stale `WAITING` + GitHub Client ID follow-ups.
4. BuildLog directory-layout migration (after the workspace stabilizes).
5. RC build → installed-app dogfood → release (after reconciliation).

## RELEASE / PRODUCT GATES

- Human approval required before: merge, tag, release, publish, real OAuth, paid
  model calls, or replacing `/Applications/SoloScale AI OS.app`.
- One writer per worktree; stage only the current slice.
- Never run `git clean -fdx` in this mixed dirty worktree.
