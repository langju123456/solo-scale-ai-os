#!/usr/bin/env python3
"""Capture Canvas courses through a visible, user-controlled browser.

This tool is intentionally private and local. It never stores passwords,
tokens, cookies, localStorage, sessionStorage, request headers, or browser
profile data in the archive. The persistent Playwright profile lives outside
the archive at .canvas_playwright_profile/ and is ignored by Git.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import select
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, unquote, urlencode, urlparse, urlunparse


NAV_TARGETS = {
    "home": "",
    "syllabus": "assignments/syllabus",
    "modules": "modules",
    "pages": "pages",
    "assignments": "assignments",
    "files": "files",
    "announcements": "announcements",
    "discussions": "discussion_topics",
}

QUIZ_PATH_RE = re.compile(r"/courses/(?P<course_id>\d+)/quizzes(?:/|$)")
COURSE_PATH_RE = re.compile(r"/courses/(?P<course_id>\d+)(?:/|$)")
FILE_PATH_RE = re.compile(r"/files/(?P<file_id>\d+)(?:/download)?(?:/|$)")
SAFE_TITLE_RE = re.compile(r"[^a-zA-Z0-9._-]+")
UNSAFE_OR_UNRESOLVED_PATH_PARTS = (
    "/api/",
    "/external_tools/",
    "/users",
    "/originality_report/",
)
UNSAFE_OR_UNRESOLVED_TEXT_PARTS = (
    "{{",
    "%7b%7b",
    "duplicate",
    "remove",
    "share to commons",
    "revert to original score",
)
CONTENT_EXCLUDED_PATH_PARTS = (
    "/grades",
    "/rubrics",
)
GITHUB_RESERVED_OWNERS = {
    "about",
    "apps",
    "collections",
    "contact",
    "customer-stories",
    "enterprise",
    "events",
    "explore",
    "features",
    "issues",
    "login",
    "marketplace",
    "new",
    "notifications",
    "organizations",
    "orgs",
    "pricing",
    "pulls",
    "search",
    "security",
    "settings",
    "signup",
    "site",
    "sponsors",
    "topics",
}
STOP_REQUESTED = False


class CaptureCancelled(BaseException):
    pass


@dataclass
class CaptureRecord:
    object_type: str
    title: str
    source_url: str
    relative_path: str | None
    status: str
    sha256: str | None = None
    canvas_id: str | None = None
    module_title: str | None = None
    module_position: int | None = None
    item_position: int | None = None
    captured_at: str | None = None
    note: str | None = None


@dataclass
class CourseRef:
    course_id: str
    title: str
    url: str


class LinkExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        for name, value in attrs:
            if name.lower() == "href" and value:
                self.links.add(value)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def request_stop(signum, frame) -> None:
    del frame
    global STOP_REQUESTED
    if STOP_REQUESTED:
        signal.default_int_handler(signum, None)
    STOP_REQUESTED = True
    print("\nStop requested. Finishing the current browser operation and closing cleanly...")
    print("Press Ctrl+C again only if you need to force an immediate exit.")


def check_stop_requested() -> None:
    if STOP_REQUESTED:
        raise CaptureCancelled()


def slugify(value: str, fallback: str = "item") -> str:
    slug = SAFE_TITLE_RE.sub("_", value.strip()).strip("._-").lower()
    return slug[:120] or fallback


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_text(path: Path, text: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return sha256_file(path)


def write_text_if_changed(path: Path, text: str) -> tuple[str, bool]:
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = sha256_text(text)
    if path.exists() and sha256_file(path) == digest:
        return digest, False
    path.write_text(text, encoding="utf-8")
    return digest, True


def write_json(path: Path, payload: object) -> str:
    return write_text(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")



def require_playwright():
    try:
        from playwright.sync_api import Error, TimeoutError, sync_playwright
    except ImportError as exc:
        raise SystemExit(
            "Playwright is required for Canvas browser capture.\n"
            "Install it locally with:\n"
            "  .venv/bin/pip install playwright\n"
            "Then use system Chrome with:\n"
            "  --browser-channel chrome\n"
            "Or install Playwright's bundled Chromium with:\n"
            "  .venv/bin/python -m playwright install chromium\n"
            "Then rerun this script."
        ) from exc
    return Error, TimeoutError, sync_playwright


def parse_course_url(course_url: str) -> tuple[str, str]:
    parsed = urlparse(course_url)
    match = COURSE_PATH_RE.search(parsed.path)
    if not parsed.scheme or not parsed.netloc or not match:
        raise SystemExit("Course URL must look like https://<canvas-host>/courses/<course_id>")
    return f"{parsed.scheme}://{parsed.netloc}", match.group("course_id")


def parse_canvas_origin(canvas_url: str) -> str:
    parsed = urlparse(canvas_url)
    if not parsed.scheme or not parsed.netloc:
        raise SystemExit("Canvas URL must look like https://<canvas-host>")
    return f"{parsed.scheme}://{parsed.netloc}"


def same_course_url(url: str, canvas_origin: str, course_id: str) -> bool:
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    match = COURSE_PATH_RE.search(parsed.path)
    return origin == canvas_origin and match is not None and match.group("course_id") == course_id


def same_origin_file_url(url: str, canvas_origin: str) -> bool:
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    return origin == canvas_origin and FILE_PATH_RE.search(parsed.path) is not None


def same_origin_file_folder_url(url: str, canvas_origin: str, course_id: str) -> bool:
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    base = f"/courses/{course_id}/files"
    return origin == canvas_origin and (
        parsed.path.rstrip("/") == base or parsed.path.startswith(f"{base}/folder/")
    )


def is_downloadable_url(url: str, canvas_origin: str) -> bool:
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if origin != canvas_origin:
        return False
    if FILE_PATH_RE.search(parsed.path):
        return True
    download_ids = parse_qs(parsed.query).get("download", [])
    return any(value.isdigit() for value in download_ids)


def direct_download_url(url: str) -> str:
    parsed = urlparse(url)
    match = FILE_PATH_RE.search(parsed.path)
    if not match:
        return url
    path = f"{parsed.path[:match.start()]}/files/{match.group('file_id')}/download"
    query = [(key, value) for key, value in parse_qsl(parsed.query) if key not in {"wrap", "download_frd"}]
    query.append(("download_frd", "1"))
    return urlunparse(parsed._replace(path=path, query=urlencode(query), fragment=""))


def download_identifier(url: str) -> str | None:
    parsed = urlparse(url)
    for value in parse_qs(parsed.query).get("download", []):
        if value.isdigit():
            return value
    match = FILE_PATH_RE.search(parsed.path)
    return match.group("file_id") if match else extract_canvas_id(url)


def canonical_content_url(url: str) -> str:
    parsed = urlparse(url)
    if FILE_PATH_RE.search(parsed.path) or parse_qs(parsed.query).get("download"):
        return urlunparse(parsed._replace(fragment=""))
    return urlunparse(parsed._replace(query="", fragment=""))


def should_skip_or_mark_unresolved(url: str, text: str = "") -> tuple[bool, str | None]:
    parsed = urlparse(url)
    lowered_url = url.lower()
    lowered_text = text.lower()
    if parsed.fragment and not parsed.path.rstrip("/").endswith("/courses"):
        return True, "fragment-only or in-page action link"
    if any(part in parsed.path for part in UNSAFE_OR_UNRESOLVED_PATH_PARTS):
        return True, "unsafe or external/LTI/unrelated Canvas path"
    if any(part in parsed.path for part in CONTENT_EXCLUDED_PATH_PARTS):
        return True, "excluded by content-only scope"
    if any(part in lowered_url or part in lowered_text for part in UNSAFE_OR_UNRESOLVED_TEXT_PARTS):
        return True, "template, menu action, or unsafe generated link"
    return False, None


def make_course_url(canvas_origin: str, course_id: str, target: str) -> str:
    base = f"{canvas_origin}/courses/{course_id}"
    return base if not target else f"{base}/{target}"


def course_url(canvas_origin: str, course_id: str) -> str:
    return f"{canvas_origin}/courses/{course_id}"


def split_ids(value: str | None) -> set[str]:
    if not value:
        return set()
    return {item.strip() for item in re.split(r"[\s,]+", value) if item.strip()}


def parse_sections(value: str) -> set[str]:
    requested = {item.strip().lower() for item in re.split(r"[\s,]+", value) if item.strip()}
    if not requested or requested == {"all"}:
        return set(NAV_TARGETS)
    unknown = requested - set(NAV_TARGETS)
    if unknown:
        choices = ", ".join(sorted(NAV_TARGETS))
        raise argparse.ArgumentTypeError(f"unknown section(s): {', '.join(sorted(unknown))}; choose from {choices}")
    return requested



def extract_canvas_id(url: str) -> str | None:
    parsed = urlparse(url)
    numbers = re.findall(r"/(\d+)(?:/|$)", parsed.path)
    if numbers:
        return numbers[-1]
    query = parse_qs(parsed.query)
    for values in query.values():
        for value in values:
            if value.isdigit():
                return value
    return None


def page_title(page) -> str:
    try:
        title = page.locator("h1").first.text_content(timeout=1500)
        if title and title.strip():
            return title.strip()
    except Exception:
        pass
    try:
        title = page.title()
        if title:
            return title.strip()
    except Exception:
        pass
    return "Untitled"


def safe_goto(page, url: str, wait_ms: int) -> bool:
    check_stop_requested()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(wait_ms)
    except Exception:
        return False
    check_stop_requested()
    return True


def parse_worker_count(value: str) -> int:
    try:
        workers = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("workers must be an integer from 1 to 10") from exc
    if not 1 <= workers <= 10:
        raise argparse.ArgumentTypeError("workers must be from 1 to 10")
    return workers


def ensure_worker_pages(page, worker_count: int) -> list:
    pages = [page]
    for _ in range(worker_count - 1):
        pages.append(page.context.new_page())
    return pages


def load_page_batch(worker_pages: list, jobs: list[tuple[str, str, str]], wait_ms: int):
    """Start several navigations, then collect them after they load in parallel."""
    started = []
    for worker_page, (job_id, title, url) in zip(worker_pages, jobs):
        check_stop_requested()
        error = None
        try:
            worker_page.goto(url, wait_until="commit", timeout=45000)
        except Exception as exc:
            error = str(exc)
        check_stop_requested()
        started.append((worker_page, job_id, title, url, error))

    loaded = []
    for worker_page, job_id, title, url, error in started:
        check_stop_requested()
        if error is None:
            try:
                worker_page.wait_for_load_state("domcontentloaded", timeout=45000)
                worker_page.wait_for_timeout(wait_ms)
            except Exception as exc:
                error = str(exc)
        check_stop_requested()
        loaded.append((worker_page, job_id, title, url, error))
    return loaded


def collect_course_links(page, canvas_origin: str, course_id: str) -> list[dict[str, str]]:
    links = page.locator("a[href]").evaluate_all(
        """anchors => anchors.map(a => ({
            href: a.href,
            text: (a.innerText || a.textContent || '').trim(),
            aria: a.getAttribute('aria-label') || '',
            title: a.getAttribute('title') || ''
        }))"""
    )
    discovered: dict[str, dict[str, str]] = {}
    for link in links:
        href = link.get("href") or ""
        text = link.get("text") or link.get("aria") or link.get("title") or href
        skip, _ = should_skip_or_mark_unresolved(href, text)
        if not skip and (same_course_url(href, canvas_origin, course_id) or is_downloadable_url(href, canvas_origin)):
            discovered[href] = {
                "url": href,
                "text": text,
            }
    return [discovered[key] for key in sorted(discovered)]


def collect_file_tree_links(page, canvas_origin: str, course_id: str, wait_ms: int) -> list[dict[str, str]]:
    root = make_course_url(canvas_origin, course_id, NAV_TARGETS["files"])
    pending = [root]
    visited: set[str] = set()
    files: dict[str, dict[str, str]] = {}
    while pending:
        check_stop_requested()
        listing_url = pending.pop(0)
        if listing_url in visited:
            continue
        visited.add(listing_url)
        if not safe_goto(page, listing_url, wait_ms):
            continue
        links = page.locator("a[href]").evaluate_all(
            """anchors => anchors.map(a => ({
                href: a.href,
                text: (a.innerText || a.textContent || '').trim(),
                aria: a.getAttribute('aria-label') || '',
                title: a.getAttribute('title') || ''
            }))"""
        )
        for link in links:
            href = link.get("href") or ""
            title = link.get("text") or link.get("aria") or link.get("title") or href
            skip, _ = should_skip_or_mark_unresolved(href, title)
            if skip:
                continue
            if is_downloadable_url(href, canvas_origin):
                files[href] = {"url": href, "text": title}
            elif same_origin_file_folder_url(href, canvas_origin, course_id) and href not in visited:
                pending.append(href)
    print(f"  Files: found {len(files)} downloadable item(s) in {len(visited)} listing page(s).")
    return [files[key] for key in sorted(files)]


def collect_all_course_refs(page, canvas_origin: str) -> list[CourseRef]:
    links = page.locator("a[href]").evaluate_all(
        """anchors => anchors.map(a => ({
            href: a.href,
            text: (a.innerText || a.textContent || '').trim(),
            aria: a.getAttribute('aria-label') || '',
            title: a.getAttribute('title') || ''
        }))"""
    )
    courses: dict[str, CourseRef] = {}
    for link in links:
        href = link.get("href") or ""
        parsed = urlparse(href)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        match = COURSE_PATH_RE.search(parsed.path)
        if origin != canvas_origin or not match:
            continue
        course_id = match.group("course_id")
        text = (link.get("text") or link.get("aria") or link.get("title") or "").strip()
        if not text or text.lower() in {"home", "courses"}:
            text = f"Canvas Course {course_id}"
        courses[course_id] = CourseRef(course_id=course_id, title=text, url=course_url(canvas_origin, course_id))
    return [courses[key] for key in sorted(courses, key=lambda item: int(item) if item.isdigit() else item)]


def select_courses(courses: list[CourseRef], include_ids: set[str], exclude_ids: set[str]) -> list[CourseRef]:
    selected = courses
    if include_ids:
        selected = [course for course in selected if course.course_id in include_ids]
    if exclude_ids:
        selected = [course for course in selected if course.course_id not in exclude_ids]
    return selected



def classify_link(url: str, course_id: str) -> str:
    parsed = urlparse(url)
    path = parsed.path
    if FILE_PATH_RE.search(path) or any(value.isdigit() for value in parse_qs(parsed.query).get("download", [])):
        return "file"
    if f"/courses/{course_id}/pages/" in path:
        return "page"
    if f"/courses/{course_id}/assignments/" in path:
        return "assignment"
    if f"/courses/{course_id}/discussion_topics/" in path:
        return "discussion"
    if f"/courses/{course_id}/announcements/" in path:
        return "announcement"
    if QUIZ_PATH_RE.search(path):
        return "quiz_metadata"
    return "course_page"


def normalize_github_repo_url(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.netloc.lower() not in {"github.com", "www.github.com"}:
        return None
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    if len(parts) < 2 or parts[0].lower() in GITHUB_RESERVED_OWNERS:
        return None
    owner, repo = parts[:2]
    repo = repo.removesuffix(".git")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", owner) or not re.fullmatch(r"[A-Za-z0-9_.-]+", repo):
        return None
    return f"https://github.com/{owner}/{repo}.git"


def discover_github_repositories(output: Path) -> list[str]:
    repositories: set[str] = set()
    for html_path in output.rglob("*.html"):
        if "external" in html_path.parts:
            continue
        parser = LinkExtractor()
        try:
            parser.feed(html_path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        for link in parser.links:
            repository = normalize_github_repo_url(link)
            if repository:
                repositories.add(repository)
    return sorted(repositories)


def git_commit(path: Path) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def archive_github_repositories(output: Path) -> list[CaptureRecord]:
    records: list[CaptureRecord] = []
    repositories = discover_github_repositories(output)
    metadata: list[dict[str, str | None]] = []
    for repository in repositories:
        check_stop_requested()
        parts = [part for part in urlparse(repository).path.removesuffix(".git").split("/") if part]
        destination = output / "external" / "github" / f"{slugify(parts[0])}__{slugify(parts[1])}"
        status = "unchanged"
        note: str | None = None
        existing_commit = git_commit(destination) if destination.exists() else None
        if destination.exists() and not existing_commit:
            backup = destination.with_name(f"{destination.name}_incomplete_{int(time.time())}")
            destination.rename(backup)
            note = f"preserved incomplete checkout at {backup.relative_to(output)} and retried"
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            print(f"  GitHub: cloning {repository.removesuffix('.git')} ...")
            result = subprocess.run(
                ["git", "clone", "--depth", "1", "--no-tags", repository, str(destination)],
                capture_output=True,
                text=True,
                check=False,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
            if result.returncode == 0:
                status = "captured"
            else:
                status = "failed"
                clone_error = (result.stderr or result.stdout).strip() or "git clone failed"
                note = f"{note}; {clone_error}" if note else clone_error
        commit = git_commit(destination) if destination.exists() else None
        if status != "failed" and not commit:
            status = "failed"
            note = f"{note}; checkout has no commit" if note else "checkout has no commit"
        metadata.append({"repository": repository.removesuffix(".git"), "commit": commit, "status": status, "note": note})
        records.append(
            CaptureRecord(
                object_type="github_repository",
                title=f"{parts[0]}/{parts[1]}",
                source_url=repository.removesuffix(".git"),
                relative_path=destination.relative_to(output).as_posix() if destination.exists() else None,
                status=status,
                sha256=commit,
                captured_at=utc_now(),
                note=note,
            )
        )
    write_json(output / "metadata" / "github_repositories.json", metadata)
    return records


def archive_html(page, output: Path, object_type: str, title: str, url: str) -> CaptureRecord:
    filename = f"{slugify(title, object_type)}_{slugify(extract_canvas_id(url) or 'page')}.html"
    relative = Path(object_type) / filename
    html = page.content()
    sha, changed = write_text_if_changed(output / relative, html)
    return CaptureRecord(
        object_type=object_type,
        title=title,
        source_url=url,
        relative_path=relative.as_posix(),
        status="captured" if changed else "unchanged",
        sha256=sha,
        canvas_id=extract_canvas_id(url),
        captured_at=utc_now(),
    )


def screenshot_page(page, output: Path, object_type: str, title: str) -> CaptureRecord | None:
    try:
        relative = Path("screenshots") / object_type / f"{slugify(title, object_type)}.png"
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(destination), full_page=True, timeout=15000)
        return CaptureRecord(
            object_type=f"{object_type}_screenshot",
            title=title,
            source_url=page.url,
            relative_path=relative.as_posix(),
            status="captured",
            sha256=sha256_file(destination),
            canvas_id=extract_canvas_id(page.url),
            captured_at=utc_now(),
        )
    except Exception as exc:
        return CaptureRecord(
            object_type=f"{object_type}_screenshot",
            title=title,
            source_url=page.url,
            relative_path=None,
            status="failed",
            captured_at=utc_now(),
            note=str(exc),
        )


def discover_links(
    page,
    canvas_origin: str,
    course_id: str,
    wait_ms: int,
    worker_pages: list | None = None,
    sections: set[str] | None = None,
) -> dict[str, list[dict[str, str]]]:
    discovered: dict[str, list[dict[str, str]]] = {}
    workers = worker_pages or [page]
    selected_sections = sections or set(NAV_TARGETS)
    targets = [
        (name, name, make_course_url(canvas_origin, course_id, target))
        for name, target in NAV_TARGETS.items()
        if name in selected_sections
    ]
    print(f"  Discovering {len(targets)} course sections with {len(workers)} tab(s)...")
    for offset in range(0, len(targets), len(workers)):
        batch = targets[offset : offset + len(workers)]
        for worker_page, name, _, _, error in load_page_batch(workers, batch, wait_ms):
            if error is None:
                if name == "files":
                    discovered[name] = collect_file_tree_links(worker_page, canvas_origin, course_id, wait_ms)
                else:
                    discovered[name] = collect_course_links(worker_page, canvas_origin, course_id)
            else:
                discovered[name] = []
    return discovered


def module_items(page, canvas_origin: str, course_id: str) -> list[dict[str, object]]:
    modules = []
    candidates = page.locator(".context_module, [data-module-id], li.context_module").all()
    for module_index, module in enumerate(candidates, start=1):
        try:
            title = module.locator(".name, .ig-header-title, h2, h3").first.inner_text(timeout=1000).strip()
        except Exception:
            title = f"Module {module_index}"
        links = module.locator("a[href]").evaluate_all(
            """anchors => anchors.map((a, index) => ({
                position: index + 1,
                href: a.href,
                title: (a.innerText || a.textContent || '').trim()
            }))"""
        )
        items = []
        for link in links:
            href = link.get("href") or ""
            if same_course_url(href, canvas_origin, course_id) or is_downloadable_url(href, canvas_origin):
                items.append(
                    {
                        "position": link.get("position"),
                        "title": link.get("title") or href,
                        "url": href,
                        "object_type": classify_link(href, course_id),
                    }
                )
        modules.append({"position": module_index, "title": title, "items": items})
    return modules


def download_file(page, url: str, output: Path, title: str, wait_ms: int) -> CaptureRecord:
    check_stop_requested()
    try:
        target_url = direct_download_url(url)
        file_id = download_identifier(url) or "file"
        safe_name = slugify(unquote(Path(urlparse(target_url).path).name) or title, "file")
        if "." not in safe_name:
            safe_name = f"{safe_name}.download"
        relative = Path("files") / f"{file_id}_{safe_name}"
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        with page.expect_download(timeout=60000) as download_info:
            try:
                page.goto(target_url, wait_until="commit", timeout=60000)
            except Exception as exc:
                if "Download is starting" not in str(exc):
                    raise
        check_stop_requested()
        download = download_info.value
        suggested = slugify(download.suggested_filename or safe_name, safe_name)
        destination = destination.with_name(f"{file_id}_{suggested}")
        download.save_as(str(destination))
        page.wait_for_timeout(wait_ms)
        check_stop_requested()
        return CaptureRecord(
            object_type="file",
            title=title,
            source_url=url,
            relative_path=destination.relative_to(output).as_posix(),
            status="captured",
            sha256=sha256_file(destination),
            canvas_id=file_id,
            captured_at=utc_now(),
        )
    except Exception as exc:
        return CaptureRecord(
            object_type="file",
            title=title,
            source_url=url,
            relative_path=None,
            status="failed",
            canvas_id=download_identifier(url),
            captured_at=utc_now(),
            note=str(exc),
        )


def write_capture_reports(output: Path, records: list[CaptureRecord], discovered: dict[str, list[dict[str, str]]]) -> None:
    counts: dict[str, dict[str, int]] = {}
    for record in records:
        counts.setdefault(record.object_type, {}).setdefault(record.status, 0)
        counts[record.object_type][record.status] += 1

    completeness = [
        "# Canvas Capture Completeness",
        "",
        f"Captured at: {utc_now()}",
        "",
        "## Status Counts",
        "",
    ]
    for object_type in sorted(counts):
        status_text = ", ".join(f"{status}: {count}" for status, count in sorted(counts[object_type].items()))
        completeness.append(f"- {object_type}: {status_text}")
    completeness.extend(["", "## Navigation Discovery", ""])
    for nav_name in sorted(discovered):
        completeness.append(f"- {nav_name}: {len(discovered[nav_name])} links")
    write_text(output / "CAPTURE_COMPLETENESS.md", "\n".join(completeness) + "\n")

    missing = [
        "# Missing Or Unresolved Canvas Items",
        "",
        "Review these before using the archive for professional evidence extraction.",
        "",
    ]
    for record in records:
        if record.status in {"failed", "skipped"}:
            missing.append(f"- {record.object_type}: `{record.title}` at {record.source_url} ({record.status}; {record.note or 'no note'})")
    missing.extend(
        [
            "",
            "## Expected Manual Checks",
            "",
            "- Confirm whether assignment submissions and their attachments are visible in Canvas.",
            "- Save external/LTI content manually if it is course content and access is permitted.",
            "- Do not use quiz attempt pages. Only quiz metadata should be archived.",
        ]
    )
    write_text(output / "MISSING_ITEMS.md", "\n".join(missing) + "\n")


def save_archive_manifest(
    output: Path,
    course_url: str,
    canvas_origin: str,
    course_id: str,
    records: list[CaptureRecord],
    discovered: dict[str, list[dict[str, str]]],
) -> None:
    write_json(
        output / "capture_manifest.json",
        {
            "course_url": course_url,
            "canvas_origin": canvas_origin,
            "course_id": course_id,
            "captured_at": utc_now(),
            "security_note": (
                "Archive intentionally excludes credentials, cookies, tokens, "
                "authorization headers, localStorage, and sessionStorage."
            ),
            "records": [asdict(record) for record in records],
            "discovered": discovered,
        },
    )


def launch_context(
    sync_playwright,
    profile_dir: Path,
    browser_channel: str | None = None,
    executable_path: str | None = None,
):
    profile_dir.mkdir(parents=True, exist_ok=True)
    playwright = sync_playwright().start()
    launch_options = {
        "user_data_dir": str(profile_dir),
        "headless": False,
        "accept_downloads": True,
        "viewport": {"width": 1440, "height": 1000},
    }
    if browser_channel:
        launch_options["channel"] = browser_channel
    if executable_path:
        launch_options["executable_path"] = executable_path
    context = playwright.chromium.launch_persistent_context(**launch_options)
    return playwright, context


def shutdown_browser(playwright, context) -> None:
    try:
        context.close()
    except Exception as exc:
        print(f"Browser context cleanup warning: {exc}")
    finally:
        try:
            playwright.stop()
        except Exception as exc:
            print(f"Playwright cleanup warning: {exc}")


def open_context_pages(context) -> list:
    pages = []
    for candidate in context.pages:
        try:
            if candidate.is_closed():
                continue
        except Exception:
            pass
        pages.append(candidate)
    return pages


def find_canvas_page(context, canvas_origin: str):
    for candidate in reversed(open_context_pages(context)):
        parsed = urlparse(candidate.url)
        if f"{parsed.scheme}://{parsed.netloc}" == canvas_origin:
            return candidate
    return None


def find_selected_course_page(context, canvas_origin: str, course_id: str):
    for candidate in reversed(open_context_pages(context)):
        if same_course_url(candidate.url, canvas_origin, course_id):
            return candidate
    return None


def visible_page_urls(context) -> list[str]:
    return [page.url for page in open_context_pages(context)]


def latest_open_page(context, fallback):
    pages = open_context_pages(context)
    return pages[-1] if pages else fallback


def maybe_follow_pasted_url(page, response: str, canvas_origin: str) -> bool:
    pasted = response.strip()
    if not pasted.startswith("http"):
        return False
    parsed = urlparse(pasted)
    if f"{parsed.scheme}://{parsed.netloc}" != canvas_origin:
        print(f"Ignoring pasted URL outside Canvas origin: {pasted}")
        return False
    print(f"Opening pasted Canvas URL in the controlled browser: {pasted}")
    return safe_goto(page, pasted, wait_ms=1000)


def read_terminal_response() -> str | None:
    """Read a completed terminal line without blocking browser event handling."""
    try:
        readable, _, _ = select.select([sys.stdin], [], [], 0)
    except (OSError, ValueError):
        return None
    if not readable:
        return None
    line = sys.stdin.readline()
    if not line:
        return None
    return line.strip()


def pump_browser_events(context, preferred_page, wait_ms: int = 250) -> None:
    """Give Playwright time to receive popup, tab, and navigation events."""
    candidates = list(reversed(open_context_pages(context)))
    if all(candidate is not preferred_page for candidate in candidates):
        candidates.append(preferred_page)
    for candidate in candidates:
        try:
            candidate.wait_for_timeout(wait_ms)
            check_stop_requested()
            return
        except Exception:
            continue
    time.sleep(wait_ms / 1000)
    check_stop_requested()


def show_visible_pages(context) -> None:
    urls = visible_page_urls(context)
    if not urls:
        print("- no open tabs in the controlled browser")
        return
    for url in urls:
        print(f"- {url}")


def finish_detected_login(page):
    try:
        page.wait_for_load_state("domcontentloaded", timeout=5000)
    except Exception:
        pass
    page.bring_to_front()
    print(f"\nCanvas detected automatically: {page.url}")
    return page


def wait_for_manual_login(context, page, course_url: str, canvas_origin: str, course_id: str):
    page.goto(course_url, wait_until="domcontentloaded", timeout=60000)
    check_stop_requested()
    print("\nA visible browser is open.")
    print("Complete Canvas SSO/Duo in the Chrome window opened by this command.")
    print("The script will continue automatically when the selected course appears.")
    print("If needed, paste the Canvas course URL here and press Enter.")
    print("Press Enter by itself to show the tabs visible to the script.")
    while True:
        selected_page = find_selected_course_page(context, canvas_origin, course_id)
        if selected_page:
            return finish_detected_login(selected_page)
        response = read_terminal_response()
        if response is not None:
            if response:
                maybe_follow_pasted_url(latest_open_page(context, page), response, canvas_origin)
            else:
                print("\nTabs currently visible to the script:")
                show_visible_pages(context)
                print(f"Waiting for: {canvas_origin}/courses/{course_id}")
        pump_browser_events(context, page)


def wait_for_canvas_login(context, page, canvas_origin: str):
    page.goto(f"{canvas_origin}/courses", wait_until="domcontentloaded", timeout=60000)
    check_stop_requested()
    print("\nA visible browser is open.")
    print("Complete Canvas SSO/Duo in the Chrome window opened by this command.")
    print("The script will continue automatically when Canvas appears.")
    print("If needed, paste a Canvas URL here and press Enter.")
    print("Press Enter by itself to show the tabs visible to the script.")
    while True:
        selected_page = find_canvas_page(context, canvas_origin)
        if selected_page:
            return finish_detected_login(selected_page)
        response = read_terminal_response()
        if response is not None:
            if response:
                maybe_follow_pasted_url(latest_open_page(context, page), response, canvas_origin)
            else:
                print("\nTabs currently visible to the script:")
                show_visible_pages(context)
                print(f"Waiting for a page under: {canvas_origin}")
        pump_browser_events(context, page)


def command_discover(args: argparse.Namespace) -> None:
    _, _, sync_playwright = require_playwright()
    canvas_origin, course_id = parse_course_url(args.course_url)
    playwright, context = launch_context(
        sync_playwright,
        args.profile_dir.expanduser().resolve(),
        args.browser_channel,
        str(args.executable_path.expanduser().resolve()) if args.executable_path else None,
    )
    try:
        page = context.pages[0] if context.pages else context.new_page()
        page = wait_for_manual_login(context, page, args.course_url, canvas_origin, course_id)
        discovered = discover_links(page, canvas_origin, course_id, args.delay_ms)
        summary = {
            "course_id": course_id,
            "canvas_origin": canvas_origin,
            "current_url": page.url,
            "course_title": page_title(page),
            "discovered_counts": {key: len(value) for key, value in discovered.items()},
            "discovered": discovered,
        }
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        print("\nDiscover created no final archive.")
    finally:
        shutdown_browser(playwright, context)


def confirm_capture(course_url: str, output: Path) -> None:
    print("\nCanvas capture scope:")
    print(f"- One course URL only: {course_url}")
    print(f"- Output directory: {output}")
    print("- Content only: course pages, assignments/submissions, files, and linked GitHub repositories.")
    print("- Grades and rubrics are excluded.")
    print("- No quiz attempts, posting, editing, uploading, deleting, or submitting.")
    response = input("Type CAPTURE to continue: ").strip()
    if response != "CAPTURE":
        raise SystemExit("Capture cancelled.")


def confirm_capture_all(courses: list[CourseRef], output_root: Path) -> None:
    print("\nCanvas batch capture scope:")
    print(f"- Courses selected: {len(courses)}")
    print(f"- Output root: {output_root}")
    for course in courses:
        print(f"  - {course.course_id}: {course.title}")
    print("- Content only: course pages, assignments/submissions, files, and linked GitHub repositories.")
    print("- Grades and rubrics are excluded.")
    print("- No quiz attempts, posting, editing, uploading, deleting, or submitting.")
    response = input("Type CAPTURE_ALL to continue: ").strip()
    if response != "CAPTURE_ALL":
        raise SystemExit("Batch capture cancelled.")


def capture_course(
    page,
    course_ref: CourseRef,
    canvas_origin: str,
    output: Path,
    delay_ms: int,
    max_pages: int,
    max_files: int,
    force: bool = False,
    worker_pages: list | None = None,
    sections: set[str] | None = None,
    capture_github: bool = True,
) -> tuple[list[CaptureRecord], dict[str, list[dict[str, str]]]]:
    manifest_path = output / "capture_manifest.json"
    if manifest_path.exists() and not force:
        print(f"Skipping already captured course {course_ref.course_id}: {output}")
        return [], {}

    output.mkdir(parents=True, exist_ok=True)
    records: list[CaptureRecord] = []
    discovered: dict[str, list[dict[str, str]]] = {}
    course_id = course_ref.course_id
    workers = worker_pages or [page]
    selected_sections = sections or set(NAV_TARGETS)

    if not safe_goto(page, course_ref.url, delay_ms):
        records.append(
            CaptureRecord("home", course_ref.title, course_ref.url, None, "failed", captured_at=utc_now())
        )
        save_archive_manifest(output, course_ref.url, canvas_origin, course_id, records, discovered)
        write_capture_reports(output, records, discovered)
        return records, discovered

    metadata = {
        "course_url": course_ref.url,
        "canvas_origin": canvas_origin,
        "course_id": course_id,
        "course_title": page_title(page),
        "sections": sorted(selected_sections),
        "github_capture": capture_github,
        "captured_at": utc_now(),
    }
    write_json(output / "metadata" / "course.json", metadata)
    records.append(archive_html(page, output, "home", metadata["course_title"], page.url))
    screenshot = screenshot_page(page, output, "home", metadata["course_title"])
    if screenshot:
        records.append(screenshot)

    discovered = discover_links(page, canvas_origin, course_id, delay_ms, workers, selected_sections)
    write_json(output / "metadata" / "discovered_urls.json", discovered)

    if "modules" in selected_sections:
        module_url = make_course_url(canvas_origin, course_id, NAV_TARGETS["modules"])
        if safe_goto(page, module_url, delay_ms):
            modules = module_items(page, canvas_origin, course_id)
            write_json(output / "metadata" / "module_order.json", modules)
            records.append(archive_html(page, output, "modules", "modules", module_url))
        else:
            records.append(
                CaptureRecord("modules", "modules", module_url, None, "failed", captured_at=utc_now())
            )

    page_urls: dict[str, str] = {}
    file_urls: dict[str, tuple[str, str]] = {}

    def add_candidate(url: str, title: str) -> None:
        skip, note = should_skip_or_mark_unresolved(url, title)
        if skip:
            if note != "excluded by content-only scope":
                records.append(
                    CaptureRecord("unresolved", title or url, url, None, "skipped", captured_at=utc_now(), note=note)
                )
            return
        object_type = classify_link(url, course_id)
        if object_type == "file":
            if "files" in selected_sections:
                key = download_identifier(url) or direct_download_url(url)
                file_urls.setdefault(key, (url, title or url))
            return
        url = canonical_content_url(url)
        if object_type == "quiz_metadata" and "/take" in urlparse(url).path:
            return
        allowed = {
            "page": bool({"pages", "modules"} & selected_sections),
            "assignment": bool({"assignments", "modules", "syllabus"} & selected_sections),
            "discussion": bool({"discussions", "modules"} & selected_sections),
            "announcement": "announcements" in selected_sections,
            "quiz_metadata": bool({"modules", "assignments"} & selected_sections),
        }
        if allowed.get(object_type, False):
            page_urls.setdefault(url, title or url)

    for section in sorted(selected_sections):
        if section in {"home", "modules", "files"}:
            continue
        add_candidate(make_course_url(canvas_origin, course_id, NAV_TARGETS[section]), section)

    for nav_links in discovered.values():
        for link in nav_links:
            add_candidate(link["url"], link.get("text") or link["url"])

    pending = sorted(page_urls)
    attempted: set[str] = set()
    while pending:
        if max_pages > 0 and len(attempted) >= max_pages:
            for url in pending:
                records.append(
                    CaptureRecord(
                        "bounded_skip",
                        page_urls[url],
                        url,
                        None,
                        "skipped",
                        captured_at=utc_now(),
                        note=f"max pages limit reached: {max_pages}",
                    )
                )
            break
        batch_size = len(workers)
        if max_pages > 0:
            batch_size = min(batch_size, max_pages - len(attempted))
        page_batch_urls = [pending.pop(0) for _ in range(min(batch_size, len(pending)))]
        page_batch = [(url, page_urls[url]) for url in page_batch_urls]
        jobs = [(url, title_hint, url) for url, title_hint in page_batch]
        start = len(attempted) + 1
        attempted.update(page_batch_urls)
        print(f"  Loading content pages {start}-{len(attempted)}; {len(pending)} queued; {len(page_batch)} tab(s)...")
        for worker_page, url, title_hint, _, error in load_page_batch(workers, jobs, delay_ms):
            if error is not None:
                records.append(
                    CaptureRecord(
                        classify_link(url, course_id),
                        title_hint,
                        url,
                        None,
                        "failed",
                        captured_at=utc_now(),
                        note=error,
                    )
                )
                continue
            title = page_title(worker_page) or title_hint
            records.append(archive_html(worker_page, output, classify_link(url, course_id), title, url))
            for link in collect_course_links(worker_page, canvas_origin, course_id):
                previous_pages = set(page_urls)
                add_candidate(link["url"], link.get("text") or link["url"])
                if link["url"] in page_urls and link["url"] not in attempted and link["url"] not in pending:
                    pending.append(link["url"])
                if set(page_urls) != previous_pages:
                    pending.sort()

    for index, (_, (url, title_hint)) in enumerate(sorted(file_urls.items()), start=1):
        if max_files > 0 and index > max_files:
            records.append(
                CaptureRecord(
                    "file",
                    title_hint,
                    url,
                    None,
                    "skipped",
                    captured_at=utc_now(),
                    note=f"max files limit reached: {max_files}",
                )
            )
            continue
        records.append(download_file(page, url, output, title_hint, delay_ms))

    if capture_github:
        records.extend(archive_github_repositories(output))

    save_archive_manifest(output, course_ref.url, canvas_origin, course_id, records, discovered)
    write_capture_reports(output, records, discovered)
    print(f"Wrote raw Canvas capture archive: {output}")
    return records, discovered

def command_discover_all(args: argparse.Namespace) -> None:
    _, _, sync_playwright = require_playwright()
    canvas_origin = parse_canvas_origin(args.canvas_url)
    playwright, context = launch_context(
        sync_playwright,
        args.profile_dir.expanduser().resolve(),
        args.browser_channel,
        str(args.executable_path.expanduser().resolve()) if args.executable_path else None,
    )
    try:
        page = context.pages[0] if context.pages else context.new_page()
        page = wait_for_canvas_login(context, page, canvas_origin)
        safe_goto(page, f"{canvas_origin}/courses", args.delay_ms)
        courses = collect_all_course_refs(page, canvas_origin)
        selected = select_courses(courses, split_ids(args.course_ids), split_ids(args.exclude_course_ids))
        payload = {
            "canvas_origin": canvas_origin,
            "current_url": page.url,
            "course_count": len(courses),
            "selected_count": len(selected),
            "courses": [asdict(course) for course in courses],
            "selected_courses": [asdict(course) for course in selected],
        }
        if args.output:
            args.output.expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
            write_json(args.output.expanduser().resolve(), payload)
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        print("\nDiscover-all created no course archives.")
    finally:
        shutdown_browser(playwright, context)


def command_capture(args: argparse.Namespace) -> None:
    _, _, sync_playwright = require_playwright()
    canvas_origin, course_id = parse_course_url(args.course_url)
    output = args.output.expanduser().resolve()
    confirm_capture(args.course_url, output)
    playwright, context = launch_context(
        sync_playwright,
        args.profile_dir.expanduser().resolve(),
        args.browser_channel,
        str(args.executable_path.expanduser().resolve()) if args.executable_path else None,
    )
    try:
        page = context.pages[0] if context.pages else context.new_page()
        page = wait_for_manual_login(context, page, args.course_url, canvas_origin, course_id)
        course_ref = CourseRef(course_id=course_id, title=page_title(page), url=args.course_url)
        worker_pages = ensure_worker_pages(page, args.workers)
        capture_course(
            page,
            course_ref,
            canvas_origin,
            output,
            args.delay_ms,
            args.max_pages,
            args.max_files,
            args.force,
            worker_pages,
            sections=args.sections,
            capture_github=args.capture_github,
        )
        print("\nNormalize it with:")
        print(f"  python3 scripts/canvas_course_archive.py {output} --course-slug canvas_course_{course_id}")
    finally:
        shutdown_browser(playwright, context)


def command_capture_all(args: argparse.Namespace) -> None:
    _, _, sync_playwright = require_playwright()
    canvas_origin = parse_canvas_origin(args.canvas_url)
    output_root = args.output_root.expanduser().resolve()
    playwright, context = launch_context(
        sync_playwright,
        args.profile_dir.expanduser().resolve(),
        args.browser_channel,
        str(args.executable_path.expanduser().resolve()) if args.executable_path else None,
    )
    global_summary: list[dict[str, object]] = []
    try:
        page = context.pages[0] if context.pages else context.new_page()
        page = wait_for_canvas_login(context, page, canvas_origin)
        safe_goto(page, f"{canvas_origin}/courses", args.delay_ms)
        courses = collect_all_course_refs(page, canvas_origin)
        selected = select_courses(courses, split_ids(args.course_ids), split_ids(args.exclude_course_ids))
        if not selected:
            raise SystemExit("No courses selected.")
        confirm_capture_all(selected, output_root)
        output_root.mkdir(parents=True, exist_ok=True)
        worker_pages = ensure_worker_pages(page, args.workers)
        for index, course in enumerate(selected, start=1):
            check_stop_requested()
            course_dir = output_root / f"{course.course_id}_{slugify(course.title, 'course')}"
            print(f"\n[{index}/{len(selected)}] Capturing {course.course_id}: {course.title}")
            records, discovered = capture_course(
                page,
                course,
                canvas_origin,
                course_dir,
                args.delay_ms,
                args.max_pages,
                args.max_files,
                args.force,
                worker_pages,
                sections=args.sections,
                capture_github=args.capture_github,
            )
            captured = sum(1 for record in records if record.status == "captured")
            skipped = sum(1 for record in records if record.status == "skipped")
            failed = sum(1 for record in records if record.status == "failed")
            global_summary.append(
                {
                    "course_id": course.course_id,
                    "title": course.title,
                    "url": course.url,
                    "output": str(course_dir),
                    "captured": captured,
                    "skipped": skipped,
                    "failed": failed,
                    "discovered_counts": {key: len(value) for key, value in discovered.items()},
                }
            )
            check_stop_requested()
        write_json(
            output_root / "GLOBAL_CAPTURE_SUMMARY.json",
            {"canvas_origin": canvas_origin, "captured_at": utc_now(), "courses": global_summary},
        )
        lines = ["# Global Canvas Capture Summary", "", f"Captured at: {utc_now()}", ""]
        for course in global_summary:
            lines.append(
                f"- {course['course_id']} {course['title']}: "
                f"captured {course['captured']}, skipped {course['skipped']}, failed {course['failed']}"
            )
        write_text(output_root / "GLOBAL_CAPTURE_SUMMARY.md", "\n".join(lines) + "\n")
        print(f"\nWrote global summary: {output_root / 'GLOBAL_CAPTURE_SUMMARY.md'}")
        print("Normalize it with:")
        print(f"  python3 scripts/canvas_course_archive.py <one course directory under {output_root}> --course-slug <course_slug>")
    finally:
        shutdown_browser(playwright, context)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    browser_common = argparse.ArgumentParser(add_help=False)
    browser_common.add_argument(
        "--profile-dir",
        type=Path,
        default=Path(".canvas_playwright_profile"),
        help="Private local Playwright profile directory, ignored by Git",
    )
    browser_common.add_argument(
        "--browser-channel",
        help="Use an installed browser channel, e.g. chrome, instead of Playwright's bundled Chromium",
    )
    browser_common.add_argument(
        "--executable-path",
        type=Path,
        help="Path to a browser executable, used when browser channel detection is unavailable",
    )
    browser_common.add_argument("--delay-ms", type=int, default=800, help="Conservative delay after page loads")

    single_course_common = argparse.ArgumentParser(add_help=False)
    single_course_common.add_argument("--course-url", required=True, help="Explicit Canvas course URL")

    all_courses_common = argparse.ArgumentParser(add_help=False)
    all_courses_common.add_argument("--canvas-url", required=True, help="Canvas origin URL, e.g. https://wustl.instructure.com")
    all_courses_common.add_argument(
        "--course-ids",
        help="Optional comma/space separated course IDs to include; default is all discovered courses",
    )
    all_courses_common.add_argument(
        "--exclude-course-ids",
        help="Optional comma/space separated course IDs to exclude",
    )

    discover = subparsers.add_parser(
        "discover",
        parents=[browser_common, single_course_common],
        help="Inspect one course navigation without archiving",
    )
    discover.set_defaults(func=command_discover)

    discover_all = subparsers.add_parser(
        "discover-all",
        parents=[browser_common, all_courses_common],
        help="List all accessible courses from Canvas Courses / All Courses",
    )
    discover_all.add_argument("--output", type=Path, help="Optional JSON path for the discovered course list")
    discover_all.set_defaults(func=command_discover_all)

    capture = subparsers.add_parser(
        "capture",
        parents=[browser_common, single_course_common],
        help="Create a raw local archive for one course",
    )
    capture.add_argument("--output", type=Path, required=True, help="Empty output directory for raw capture")
    capture.add_argument("--max-pages", type=int, default=0, help="Maximum HTML pages; 0 means unlimited (default)")
    capture.add_argument("--max-files", type=int, default=0, help="Maximum downloaded files; 0 means unlimited (default)")
    capture.add_argument(
        "--sections",
        type=parse_sections,
        default=parse_sections("all"),
        help="Comma-separated content sections or 'all'; grades/rubrics are never captured",
    )
    capture.add_argument(
        "--no-github",
        action="store_false",
        dest="capture_github",
        help="Do not clone GitHub repositories linked from captured course pages",
    )
    capture.add_argument(
        "--workers",
        type=parse_worker_count,
        default=5,
        help="Concurrent browser tabs for page capture, from 1 to 10 (default: 5)",
    )
    capture.add_argument("--force", action="store_true", help="Overwrite or refresh an existing capture directory")
    capture.set_defaults(func=command_capture)

    capture_all = subparsers.add_parser(
        "capture-all",
        parents=[browser_common, all_courses_common],
        help="Batch capture all or selected accessible courses",
    )
    capture_all.add_argument("--output-root", type=Path, required=True, help="Directory for per-course raw archives")
    capture_all.add_argument("--max-pages", type=int, default=0, help="Maximum HTML pages per course; 0 is unlimited")
    capture_all.add_argument("--max-files", type=int, default=0, help="Maximum files per course; 0 is unlimited")
    capture_all.add_argument(
        "--sections",
        type=parse_sections,
        default=parse_sections("all"),
        help="Comma-separated content sections or 'all'; grades/rubrics are never captured",
    )
    capture_all.add_argument(
        "--no-github",
        action="store_false",
        dest="capture_github",
        help="Do not clone GitHub repositories linked from captured course pages",
    )
    capture_all.add_argument(
        "--workers",
        type=parse_worker_count,
        default=5,
        help="Concurrent browser tabs for page capture, from 1 to 10 (default: 5)",
    )
    capture_all.add_argument("--force", action="store_true", help="Refresh courses that already have capture manifests")
    capture_all.set_defaults(func=command_capture_all)
    return parser.parse_args()


def main() -> None:
    global STOP_REQUESTED
    STOP_REQUESTED = False
    previous_sigint_handler = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, request_stop)
    args = parse_args()
    started = time.time()
    try:
        args.func(args)
    except CaptureCancelled:
        raise SystemExit("\nCapture stopped cleanly. Rerun the same command to continue.") from None
    except KeyboardInterrupt:
        raise SystemExit("\nForced exit requested by user.") from None
    finally:
        signal.signal(signal.SIGINT, previous_sigint_handler)
        if "--debug-timing" in sys.argv:
            print(f"Elapsed seconds: {time.time() - started:.2f}")


if __name__ == "__main__":
    main()
