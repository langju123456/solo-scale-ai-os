# Canvas Content Archive Workflow

This local utility archives course content from Canvas when personal access
tokens are unavailable. It uses a visible system Chrome window, keeps the
authenticated profile locally, and supports current and past courses.

## Captured Content

- syllabus, modules, pages, and course organization
- assignment descriptions and visible submission text
- assignment/submission attachments and course files
- announcements, discussions, and quiz metadata
- GitHub repositories linked from captured pages, including notebooks

Grades and rubrics are intentionally excluded. The browser interactions are
read-only: the tool does not submit, edit, delete, post, upload, or start a
quiz. Credentials, cookies, tokens, storage, and request headers are not saved
inside an archive.

## Browser Setup

```bash
.venv/bin/pip install playwright
```

Use installed Google Chrome with `--browser-channel chrome`; downloading
Playwright Chromium is not required.

## Discover Courses

```bash
.venv/bin/python scripts/canvas_capture.py discover-all \
  --canvas-url "https://your-school.instructure.com" \
  --browser-channel chrome
```

Complete SSO/Duo once in the Chrome window. Later runs reuse the private
`.canvas_playwright_profile/` session while it remains valid.

## Capture Content

Capture all accessible courses with five page tabs:

```bash
.venv/bin/python scripts/canvas_capture.py capture-all \
  --canvas-url "https://your-school.instructure.com" \
  --browser-channel chrome \
  --workers 5 \
  --output-root canvas_raw_captures
```

`--max-pages` and `--max-files` default to `0`, meaning unlimited. Use positive
values only when intentionally testing a small sample. Existing manifests are
skipped unless `--force` is supplied.

Select courses or content sections when needed:

```bash
.venv/bin/python scripts/canvas_capture.py capture-all \
  --canvas-url "https://your-school.instructure.com" \
  --browser-channel chrome \
  --course-ids "146374,160097" \
  --sections "syllabus,modules,pages,assignments,files" \
  --output-root canvas_raw_captures \
  --force
```

Available sections are `home`, `syllabus`, `modules`, `pages`, `assignments`,
`files`, `announcements`, and `discussions`. Grades and rubrics cannot be added
through this option.

Linked GitHub repositories are shallow-cloned by default under
`external/github/`, and exact commits are written to
`metadata/github_repositories.json`. Use `--no-github` to disable this.

## Archive Layout

Each course directory contains raw HTML grouped by content type, downloaded
files, module ordering metadata, linked GitHub repositories, a capture
manifest, and completeness/missing-item reports. File folders are traversed
recursively, and file names include Canvas IDs to prevent overwrites.

Both `.canvas_playwright_profile/` and `canvas_raw_captures/` are ignored by
Git.
