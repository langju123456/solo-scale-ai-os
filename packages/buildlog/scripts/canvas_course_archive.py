#!/usr/bin/env python3
"""Inspect a local Canvas course export without modifying the source files."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import mimetypes
import re
import shutil
import zipfile
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path


CAREER_HINTS = (
    "assignment",
    "project",
    "capstone",
    "submission",
    "syllabus",
    "resume",
    "portfolio",
    "case",
)

TYPE_HINTS = {
    "repository": ("external/github",),
    "syllabus": ("syllabus",),
    "module": ("module", "modules"),
    "assignment": ("assignment", "assignments"),
    "discussion": ("discussion", "discussions"),
    "quiz": ("quiz", "quizzes", "assessment"),
    "reading": ("reading", "readings", "file", "files", "attachment", "attachments"),
    "page": ("page", "pages", "wiki"),
    "metadata": ("manifest", "metadata", "imsmanifest", ".xml", ".json"),
    "external_link": ("external", "link", "url"),
}
CONTENT_EXCLUDED_URL_PARTS = ("/grades", "/rubrics")


@dataclass
class InventoryItem:
    relative_path: str
    file_type: str
    size: int
    sha256: str
    inferred_canvas_content_type: str
    likely_useful_for_career_evidence: bool
    derived_path: str | None


class HtmlToMarkdown(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.href_stack: list[str | None] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = dict(attrs)
        if tag in {"p", "div", "section", "article", "tr"}:
            self.parts.append("\n\n")
        elif tag in {"br", "li"}:
            self.parts.append("\n")
            if tag == "li":
                self.parts.append("- ")
        elif tag in {"h1", "h2", "h3"}:
            self.parts.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag == "a":
            self.href_stack.append(attrs_map.get("href"))
        elif tag in {"strong", "b"}:
            self.parts.append("**")
        elif tag in {"em", "i"}:
            self.parts.append("_")

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            href = self.href_stack.pop() if self.href_stack else None
            if href:
                self.parts.append(f" ({href})")
        elif tag in {"strong", "b"}:
            self.parts.append("**")
        elif tag in {"em", "i"}:
            self.parts.append("_")
        elif tag in {"h1", "h2", "h3", "p", "div", "li"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        text = html.unescape(data)
        if text.strip():
            self.parts.append(re.sub(r"\s+", " ", text))

    def markdown(self) -> str:
        text = "".join(self.parts)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip() + "\n"


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value.lower()).strip("_")
    return slug or "canvas_course"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def infer_content_type(relative_path: str) -> str:
    lowered = relative_path.lower()
    for content_type, hints in TYPE_HINTS.items():
        if any(hint in lowered for hint in hints):
            return content_type
    return "unresolved"


def is_career_useful(relative_path: str, content_type: str) -> bool:
    lowered = relative_path.lower()
    return content_type in {"syllabus", "assignment", "page", "repository"} or any(
        hint in lowered for hint in CAREER_HINTS
    )


def destination_bucket(content_type: str) -> str:
    return {
        "syllabus": "syllabus",
        "module": "modules",
        "assignment": "assignments",
        "discussion": "discussions",
        "quiz": "quizzes",
        "reading": "readings",
        "page": "modules",
        "repository": "repositories",
    }.get(content_type, "unresolved")


def extract_source(source: Path, work_dir: Path) -> Path:
    if source.is_dir():
        return source
    if source.suffix.lower() not in {".zip", ".imscc"}:
        raise SystemExit(f"Unsupported source: {source}. Use a directory, .zip, or .imscc file.")

    extract_dir = work_dir / "extracted_source"
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True)
    with zipfile.ZipFile(source) as archive:
        archive.extractall(extract_dir)
    return extract_dir


def convert_or_copy(source_file: Path, root: Path, archive_dir: Path, content_type: str) -> str:
    relative = source_file.relative_to(root)
    bucket_name = destination_bucket(content_type)
    bucket = archive_dir / bucket_name
    bucket.mkdir(parents=True, exist_ok=True)
    relative_parts = relative.parts
    if relative_parts and relative_parts[0].lower() == bucket_name:
        relative_parts = relative_parts[1:]
    safe_name = Path(*relative_parts) if relative_parts else Path(source_file.name)
    destination = bucket / safe_name
    destination.parent.mkdir(parents=True, exist_ok=True)

    if source_file.suffix.lower() in {".html", ".htm", ".xhtml"}:
        parser = HtmlToMarkdown()
        parser.feed(source_file.read_text(encoding="utf-8", errors="ignore"))
        destination = destination.with_suffix(".md")
        destination.write_text(
            f"Source: `{relative.as_posix()}`\n\n{parser.markdown()}",
            encoding="utf-8",
        )
    else:
        shutil.copy2(source_file, destination)
    return destination.relative_to(archive_dir).as_posix()


def excluded_content_paths(root: Path) -> set[str]:
    excluded: set[str] = set()
    manifest_path = root / "capture_manifest.json"
    if not manifest_path.exists():
        return excluded
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return excluded
    for record in manifest.get("records", []):
        relative_path = record.get("relative_path")
        source_url = (record.get("source_url") or "").lower()
        if relative_path and any(part in source_url for part in CONTENT_EXCLUDED_URL_PARTS):
            excluded.add(relative_path)
    return excluded


def is_content_file(path: Path, root: Path, manifest_exclusions: set[str]) -> bool:
    relative = path.relative_to(root)
    lowered_name = path.name.lower()
    if ".git" in relative.parts or relative.as_posix() in manifest_exclusions:
        return False
    if "rubric" in lowered_name:
        return False
    return not (relative.parent.name == "course_page" and lowered_name.startswith("grades_for_"))


def build_inventory(root: Path, archive_dir: Path) -> list[InventoryItem]:
    inventory: list[InventoryItem] = []
    manifest_exclusions = excluded_content_paths(root)
    source_files = (
        path
        for path in root.rglob("*")
        if path.is_file() and is_content_file(path, root, manifest_exclusions)
    )
    for source_file in sorted(source_files):
        relative_path = source_file.relative_to(root).as_posix()
        content_type = infer_content_type(relative_path)
        derived_path = convert_or_copy(source_file, root, archive_dir, content_type)
        mime_type = mimetypes.guess_type(source_file.name)[0] or source_file.suffix.lower().lstrip(".") or "unknown"
        inventory.append(
            InventoryItem(
                relative_path=relative_path,
                file_type=mime_type,
                size=source_file.stat().st_size,
                sha256=sha256_file(source_file),
                inferred_canvas_content_type=content_type,
                likely_useful_for_career_evidence=is_career_useful(relative_path, content_type),
                derived_path=derived_path,
            )
        )
    return inventory


def write_index(course_slug: str, archive_dir: Path, inventory: list[InventoryItem]) -> None:
    counts: dict[str, int] = {}
    for item in inventory:
        counts[item.inferred_canvas_content_type] = counts.get(item.inferred_canvas_content_type, 0) + 1

    lines = [
        f"# Canvas Course Index: {course_slug}",
        "",
        "This is a derived archive. Keep the original Canvas export unchanged as the source of truth.",
        "",
        "## Inventory Summary",
        "",
    ]
    for key in sorted(counts):
        lines.append(f"- {key}: {counts[key]}")

    lines.extend(["", "## Career-Relevant Candidates", ""])
    useful = [item for item in inventory if item.likely_useful_for_career_evidence]
    if useful:
        for item in useful:
            lines.append(
                f"- `{item.relative_path}` -> `{item.derived_path}` "
                f"({item.inferred_canvas_content_type}, {item.size} bytes)"
            )
    else:
        lines.append("- None detected from filenames and Canvas metadata alone.")

    lines.extend(["", "## Likely Missing Content", ""])
    missing = [
        "User submissions unless the export explicitly contains submission files.",
        "External-tool assignment content and LTI-hosted work.",
        "Submission attachments that are no longer downloadable from Canvas.",
    ]
    for item in missing:
        lines.append(f"- {item}")

    archive_dir.joinpath("COURSE_INDEX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_professional_evidence(course_slug: str, archive_dir: Path, inventory: list[InventoryItem]) -> None:
    lines = [
        f"# Course Professional Evidence: {course_slug}",
        "",
        "This is a template only. The archive inventory stage does not make",
        "professional claims about the student.",
        "",
        "Evidence labels: E1 direct artifact, E2 supported inference, E3 unverified.",
        "",
        "## Course Purpose",
        "",
        "- Pending human review of syllabus and course materials.",
        "",
        "## Topics Actually Covered",
        "",
        "- Pending human review of module, page, reading, assignment, and discussion artifacts.",
        "",
        "## Major Assignments",
        "",
        "- Pending human review of assignment prompts and submitted artifacts.",
        "",
        "## Projects The Student Personally Completed",
        "",
        "- Pending student-owned submissions, repos, notebooks, reports, decks, screenshots, or demos.",
        "",
        "## Technical Capabilities Demonstrated",
        "",
        "- Pending direct artifacts. Do not infer capability from course content alone.",
        "",
        "## AI Engineering Capabilities Demonstrated",
        "",
        "- Pending direct artifacts. Do not infer capability from lecture or syllabus topics alone.",
        "",
        "## Business/Product Capabilities Demonstrated",
        "",
        "- Pending direct artifacts.",
        "",
        "## Stakeholder Or Teamwork Evidence",
        "",
        "- Pending direct artifacts.",
        "",
        "## Decisions And Trade-Offs",
        "",
        "- Pending direct artifacts.",
        "",
        "## Instructor Feedback",
        "",
        "- Pending rubrics, comments, grades, or feedback screenshots/PDFs.",
        "",
        "## Evidence-Backed Resume Claims",
        "",
        "- Pending evidence review.",
        "",
        "## Interview Story Candidates",
        "",
        "- Pending evidence review.",
        "",
        "## Unsupported Claims",
        "",
        "- The student implemented, deployed, measured, or led work unless a direct artifact proves it.",
        "- Any metric, user count, production impact, grade, or instructor praise absent from the archive.",
        "",
        "## Missing Artifacts To Retrieve",
        "",
        "- Assignment submissions and final project files.",
        "- Rubrics, instructor comments, and grades as PDF or screenshot.",
        "- External-tool outputs, notebooks, repos, decks, reports, spreadsheets, and demos.",
        "",
        "## Archive Inventory Reference",
        "",
        f"- Files inventoried: {len(inventory)}",
        "- Review `manifest.json` and `COURSE_INDEX.md` before filling this template.",
    ]
    archive_dir.joinpath("COURSE_PROFESSIONAL_EVIDENCE.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def write_manifest(source: Path, course_slug: str, archive_dir: Path, inventory: list[InventoryItem]) -> None:
    manifest = {
        "course_slug": course_slug,
        "source": str(source),
        "source_sha256": sha256_file(source) if source.is_file() else None,
        "file_count": len(inventory),
        "inventory": [asdict(item) for item in inventory],
    }
    archive_dir.joinpath("manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Canvas course export directory, .zip, or .imscc")
    parser.add_argument("--course-slug", help="Stable slug for the derived archive directory")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("course_archive"),
        help="Directory where the derived course archive will be written",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source = args.source.expanduser().resolve()
    if not source.exists():
        raise SystemExit(f"Source does not exist: {source}")

    course_slug = slugify(args.course_slug or source.stem)
    archive_dir = args.output_root.expanduser().resolve() / course_slug
    work_dir = archive_dir / "_work"
    if archive_dir.exists():
        raise SystemExit(f"Archive already exists: {archive_dir}")
    work_dir.mkdir(parents=True, exist_ok=False)

    root = extract_source(source, work_dir)
    for folder in ["syllabus", "modules", "assignments", "readings", "discussions", "quizzes", "unresolved"]:
        archive_dir.joinpath(folder).mkdir(parents=True, exist_ok=True)

    inventory = build_inventory(root, archive_dir)
    write_manifest(source, course_slug, archive_dir, inventory)
    write_index(course_slug, archive_dir, inventory)
    write_professional_evidence(course_slug, archive_dir, inventory)
    shutil.rmtree(work_dir)
    print(f"Wrote derived Canvas archive: {archive_dir}")
    print(f"Inventoried {len(inventory)} files.")


if __name__ == "__main__":
    main()
