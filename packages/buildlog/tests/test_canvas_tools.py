from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_canvas_capture_course_url_scoping() -> None:
    capture = load_script("canvas_capture")

    origin, course_id = capture.parse_course_url("https://school.instructure.com/courses/12345/modules")

    assert origin == "https://school.instructure.com"
    assert course_id == "12345"
    assert capture.same_course_url("https://school.instructure.com/courses/12345/pages/intro", origin, course_id)
    assert not capture.same_course_url("https://school.instructure.com/courses/123456/pages/intro", origin, course_id)
    assert not capture.same_course_url("https://school.instructure.com/courses/99999/pages/intro", origin, course_id)
    assert not capture.same_course_url("https://other.example.com/courses/12345/pages/intro", origin, course_id)


def test_canvas_capture_classifies_course_links() -> None:
    capture = load_script("canvas_capture")

    assert capture.classify_link("https://school.instructure.com/courses/123/pages/intro", "123") == "page"
    assert capture.classify_link("https://school.instructure.com/courses/123/assignments/456", "123") == "assignment"
    assert capture.classify_link("https://school.instructure.com/courses/123/quizzes/456", "123") == "quiz_metadata"
    assert capture.classify_link("https://school.instructure.com/files/789/download", "123") == "file"
    assert (
        capture.classify_link(
            "https://school.instructure.com/courses/123/assignments/456/submissions/99?download=789",
            "123",
        )
        == "file"
    )


def test_canvas_capture_distinguishes_file_folders_and_direct_downloads() -> None:
    capture = load_script("canvas_capture")
    origin = "https://school.instructure.com"
    folder = f"{origin}/courses/123/files/folder/Week%201"
    preview = f"{origin}/courses/123/files/789?wrap=1&location=course_syllabus_123"

    assert capture.same_origin_file_folder_url(folder, origin, "123")
    assert not capture.same_origin_file_url(folder, origin)
    assert capture.same_origin_file_url(preview, origin)
    assert capture.direct_download_url(preview) == (
        f"{origin}/courses/123/files/789/download?location=course_syllabus_123&download_frd=1"
    )
    assert capture.canonical_content_url(
        f"{origin}/courses/123/pages/intro?module_item_id=456#content"
    ) == f"{origin}/courses/123/pages/intro"
    assert capture.canonical_content_url(preview) == preview


def test_canvas_capture_content_scope_excludes_grades_and_rubrics() -> None:
    capture = load_script("canvas_capture")

    for url in (
        "https://school.instructure.com/courses/123/grades",
        "https://school.instructure.com/courses/123/rubrics/456",
    ):
        should_skip, note = capture.should_skip_or_mark_unresolved(url)
        assert should_skip
        assert note == "excluded by content-only scope"

    assert capture.parse_sections("all") == set(capture.NAV_TARGETS)
    assert capture.parse_sections("modules, assignments, files") == {"modules", "assignments", "files"}


def test_canvas_capture_finds_github_repositories_in_saved_html(tmp_path: Path) -> None:
    capture = load_script("canvas_capture")
    page = tmp_path / "assignment" / "course.html"
    page.parent.mkdir()
    page.write_text(
        '<a href="https://github.com/jeffheaton/app_generative_ai/blob/main/README.md">course</a>'
        '<a href="https://github.com/jeffheaton/app_generative_ai/tree/main/assignments">assignments</a>'
        '<a href="https://github.com/features/actions">not a repository</a>',
        encoding="utf-8",
    )

    assert capture.discover_github_repositories(tmp_path) == [
        "https://github.com/jeffheaton/app_generative_ai.git"
    ]


def test_canvas_capture_skips_menu_lti_and_generated_links() -> None:
    capture = load_script("canvas_capture")

    unsafe_urls = [
        "https://school.instructure.com/api/v1/courses/123/modules/items/%7B%7B%20id%20%7D%7D/duplicate",
        "https://school.instructure.com/courses/123/external_tools/11514",
        "https://school.instructure.com/courses/123/users",
        "https://school.instructure.com/courses/123/assignments/1/submissions/2/originality_report/attachment_3",
        "https://school.instructure.com/courses/123/modules/items/%7B%7B%20id%20%7D%7D",
    ]

    for url in unsafe_urls:
        should_skip, note = capture.should_skip_or_mark_unresolved(url, "Duplicate")
        assert should_skip
        assert note


def test_canvas_capture_selects_all_included_and_excluded_courses() -> None:
    capture = load_script("canvas_capture")
    courses = [
        capture.CourseRef(course_id="100", title="A", url="https://school.instructure.com/courses/100"),
        capture.CourseRef(course_id="200", title="B", url="https://school.instructure.com/courses/200"),
        capture.CourseRef(course_id="300", title="C", url="https://school.instructure.com/courses/300"),
    ]

    assert [course.course_id for course in capture.select_courses(courses, set(), set())] == ["100", "200", "300"]
    assert [course.course_id for course in capture.select_courses(courses, {"200"}, set())] == ["200"]
    assert [course.course_id for course in capture.select_courses(courses, set(), {"200"})] == ["100", "300"]
    assert capture.split_ids("100, 200\n300") == {"100", "200", "300"}


def test_canvas_capture_finds_newest_open_canvas_tab() -> None:
    capture = load_script("canvas_capture")

    class FakePage:
        def __init__(self, url: str, closed: bool = False) -> None:
            self.url = url
            self.closed = closed

        def is_closed(self) -> bool:
            return self.closed

    class FakeContext:
        pages = [
            FakePage("https://login.school.edu/sso"),
            FakePage("https://school.instructure.com/courses/100", closed=True),
            FakePage("https://school.instructure.com/courses/200"),
        ]

    context = FakeContext()
    page = capture.find_canvas_page(context, "https://school.instructure.com")

    assert page is context.pages[2]
    assert capture.find_selected_course_page(context, "https://school.instructure.com", "100") is None
    assert capture.find_selected_course_page(context, "https://school.instructure.com", "200") is context.pages[2]


def test_canvas_capture_worker_count_is_bounded() -> None:
    capture = load_script("canvas_capture")

    assert capture.parse_worker_count("1") == 1
    assert capture.parse_worker_count("5") == 5
    assert capture.parse_worker_count("10") == 10

    for invalid in ("0", "11", "many"):
        try:
            capture.parse_worker_count(invalid)
        except Exception as exc:
            assert "workers" in str(exc)
        else:
            raise AssertionError(f"Expected invalid worker count: {invalid}")


def test_canvas_capture_detects_unchanged_text(tmp_path: Path) -> None:
    capture = load_script("canvas_capture")
    path = tmp_path / "page.html"

    first_hash, first_changed = capture.write_text_if_changed(path, "<h1>Course</h1>")
    second_hash, second_changed = capture.write_text_if_changed(path, "<h1>Course</h1>")

    assert first_hash == second_hash
    assert first_changed is True
    assert second_changed is False


def test_professional_evidence_is_template_only(tmp_path: Path) -> None:
    archive = load_script("canvas_course_archive")
    output = tmp_path / "archive"
    output.mkdir()

    archive.write_professional_evidence("course", output, [])

    text = output.joinpath("COURSE_PROFESSIONAL_EVIDENCE.md").read_text(encoding="utf-8")
    assert "template only" in text
    assert "Pending evidence review" in text
    assert "Syllabus candidate" not in text


def test_canvas_normalizer_keeps_repository_content_but_skips_git_metadata(tmp_path: Path) -> None:
    archive = load_script("canvas_course_archive")
    source = tmp_path / "source"
    repository = source / "external" / "github" / "owner__course"
    repository.joinpath(".git").mkdir(parents=True)
    repository.joinpath("lesson.ipynb").write_text("{}", encoding="utf-8")
    repository.joinpath(".git", "config").write_text("private git metadata", encoding="utf-8")
    grade_page = source / "course_page" / "grades_for_student_123.html"
    grade_page.parent.mkdir()
    grade_page.write_text("grade", encoding="utf-8")
    rubric_page = source / "assignment" / "rubric_456.html"
    rubric_page.parent.mkdir()
    rubric_page.write_text("rubric", encoding="utf-8")

    inventory = archive.build_inventory(source, tmp_path / "normalized")

    assert [item.relative_path for item in inventory] == ["external/github/owner__course/lesson.ipynb"]
    assert inventory[0].inferred_canvas_content_type == "repository"
    assert inventory[0].derived_path == "repositories/external/github/owner__course/lesson.ipynb"
