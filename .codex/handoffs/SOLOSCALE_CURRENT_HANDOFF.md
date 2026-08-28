# SoloScale Current Handoff

Updated: 2026-08-29

Canonical worktree: `/Users/ju.l/Documents/AI TEAM/AI TEAM WORKTREES/solo-scale-ai-os-macos-desktop-app`

## CURRENT BRANCH / HEAD

- branch: `codex/macos-desktop-app`
- HEAD: `6ffd005` (pushed to `origin/codex/macos-desktop-app`)

## COMPLETED CHECKPOINTS

- `b4ae09d` — Module A: creator production and publishing consolidation.
- `53db791` — Module B: work source control plane + advanced diagnostics.
- `4ae6b80` — Module C: learning mastery separated from claim eligibility; project
  binding + seed-case fixture decoupling; freshness preflight before case build.
- `194dc12` — Resume evidence reliability: atomic-fact admission/quarantine trace,
  canonical PDF input-quality gate with bounded pypdf fallback, cross-locale fact
  matching, and the GitHub→Resume boundary fix (verified commit facts are no longer
  silently dropped by JD relevance compaction).
- `6ffd005` — Learning decoupled from the SoloScale repo binding: removed the dead
  `_is_supported_learning_repository` helper and the desktop Choose SoloScale Source
  Checkout binding.

## CURRENT DIRTY WORK (ownership by future slice)

- Web/Vercel thread (disposition decision pending): `.vercelignore`, `api/`,
  `requirements.txt`, `src/soloscale/app_web.py`, `src/soloscale/resume_web.py`,
  `tests/test_app_web.py`, `tests/test_resume_web.py`, `vercel.json`,
  `docs/guides/`, `docs/operations/`.
- Docs refresh: `PROJECT.md`, `README.md`, `ROADMAP.md`, `TASK.md`,
  `docs/architecture.md`.

## KNOWN ISSUES

- The committed Resume slice is mypy-clean. Remaining `mypy` errors are pre-existing
  in the `buildlog` package and Creator/YouTube job-manager hunks in `local_ui.py`.
- Desktop toolchain: `check_macos_toolchain.sh` passes with full Xcode 26.6
  (Swift 6.3.3, macOS SDK 26.5).
- The installed `/Applications/SoloScale AI OS.app` may be behind source; verify
  bundle identity before claiming installed behavior.

## NEXT CANDIDATE SLICES

1. Web/Vercel disposition decision (keep as a separate branch/PR vs defer per the
   Local-MVP policy).
2. YouTube OAuth stale `WAITING` + GitHub Client ID follow-ups.
3. BuildLog directory-layout migration (after the workspace stabilizes).
4. Fresh RC build → installed-app dogfood → blocker-only repair → release.

## RELEASE / PRODUCT GATES

- Human approval required before: merge, tag, release, publish, real OAuth, paid
  model calls, or replacing `/Applications/SoloScale AI OS.app`.
- One writer per worktree; stage only the current slice.
- Never run `git clean -fdx` in this mixed dirty worktree.
