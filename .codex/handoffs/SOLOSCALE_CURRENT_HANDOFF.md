# SoloScale Current Handoff

Updated: 2026-08-29

Canonical worktree: `/Users/ju.l/Documents/AI TEAM/AI TEAM WORKTREES/solo-scale-ai-os-macos-desktop-app`

## CURRENT BRANCH / HEAD

- branch: `codex/macos-desktop-app`
- HEAD: `b55fcc7` (pushed to `origin/codex/macos-desktop-app`)

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
- `b55fcc7` — Release checkpoint 0.4.1 (6): PyInstaller spec sys.path fix so
  `content_data` is packaged when soloscale is not pip-installed; version
  normalized to `0.4.1` / build `6` in build script defaults, Info.plist
  template, pyproject, and `soloscale.__version__`. Fresh 0.4.1 (6) build
  verified (bundle version, codesign, month_one.json, pypdf).

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
- Release candidate `0.4.1 (6)` is **ad-hoc signed only**; no Developer ID
  certificate is available (`security find-identity -p codesigning` = 0
  identities), so the public distribution boundary is a GitHub
  prerelease/developer preview with SHA-256 + Gatekeeper instructions, not a
  notarized production release.
- GitHub App Client ID is not configured; UI truthfully shows "not configured"
  (not a release blocker).
- Creator Story → heavy video render was NOT_DOGFOODED in the RC run; release
  notes must not claim it was verified.

## NEXT CANDIDATE SLICES

1. **Final Release thread** from `b55fcc7`: fresh 0.4.1 (6) build → critical
   packaged smoke → DMG → codesign state → SHA-256 → release notes → Git tag →
   GitHub prerelease (Developer ID signing/notarization deferred; no cert
   available).
2. Web/Vercel disposition decision (keep as a separate branch/PR vs defer per the
   Local-MVP policy).
3. YouTube OAuth stale `WAITING` + GitHub Client ID follow-ups.
4. BuildLog directory-layout migration (after the workspace stabilizes).

## RELEASE / PRODUCT GATES

- Human approval required before: merge, tag, release, publish, real OAuth, paid
  model calls, or replacing `/Applications/SoloScale AI OS.app`.
- One writer per worktree; stage only the current slice.
- Never run `git clean -fdx` in this mixed dirty worktree.
