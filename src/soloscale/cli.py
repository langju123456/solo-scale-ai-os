from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from soloscale.buildlog_adapter import export_buildlog_iteration
from soloscale.casebook_models import (
    AttemptOutcome,
    EvidenceKind,
    LearningCase,
    PracticeStage,
)
from soloscale.casebook_store import CasebookStore
from soloscale.control_tower import build_control_tower
from soloscale.handoff import packet_from_task, render_packet_markdown
from soloscale.models import (
    LatencyTolerance,
    ReasoningDepth,
    RiskLevel,
    RunSummary,
    TaskEnvelope,
)
from soloscale.router import route_task

app = typer.Typer(no_args_is_help=True)
console = Console()


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _parse_evidence_sources(specs: list[str]) -> list[tuple[EvidenceKind, Path]]:
    sources: list[tuple[EvidenceKind, Path]] = []
    for spec in specs:
        if "=" not in spec:
            raise typer.BadParameter(
                f"invalid evidence '{spec}'; expected KIND=PATH",
                param_hint="--evidence",
            )
        raw_kind, raw_path = spec.split("=", 1)
        if not raw_kind.strip() or not raw_path.strip():
            raise typer.BadParameter(
                f"invalid evidence '{spec}'; expected non-empty KIND=PATH",
                param_hint="--evidence",
            )
        try:
            kind = EvidenceKind(raw_kind.strip().lower())
        except ValueError as exc:
            allowed = ", ".join(item.value for item in EvidenceKind)
            raise typer.BadParameter(
                f"unsupported evidence kind '{raw_kind}'; choose one of: {allowed}",
                param_hint="--evidence",
            ) from exc
        sources.append((kind, Path(raw_path)))
    if not sources:
        raise typer.BadParameter("at least one evidence file is required", param_hint="--evidence")
    return sources


def _resolve_case_target(target: str, data_root: Path) -> tuple[str, Path]:
    candidate = Path(target)
    try:
        is_file = candidate.is_file()
    except OSError as exc:
        raise typer.BadParameter("case target could not be inspected", param_hint="case") from exc

    if not is_file:
        return target, data_root

    if candidate.name != "case.json" or candidate.parent.parent.name != "cases":
        raise typer.BadParameter(
            "case file must be named case.json under <data-root>/cases/<case-id>/",
            param_hint="case",
        )

    directory_case_id = candidate.parent.name
    try:
        learning_case = LearningCase.model_validate_json(
            candidate.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(
            "case.json is not a valid Casebook manifest",
            param_hint="case",
        ) from exc

    if learning_case.id != directory_case_id:
        raise typer.BadParameter(
            "case.json id does not match its case directory",
            param_hint="case",
        )

    inferred_root = candidate.parent.parent.parent
    return learning_case.id, inferred_root


def _git_worktree_root(path: Path) -> Path | None:
    """Find the nearest Git worktree containing a prospective data root."""

    candidate = path.resolve(strict=False)
    if not candidate.is_dir():
        candidate = candidate.parent
    for ancestor in (candidate, *candidate.parents):
        if (ancestor / ".git").exists():
            return ancestor
    return None


def _validate_private_data_root(data_root: Path) -> None:
    """Keep in-repository private artifacts under the ignored .soloscale tree."""

    resolved_root = data_root.resolve(strict=False)
    worktree = _git_worktree_root(resolved_root)
    if worktree is None:
        return
    relative_parts = resolved_root.relative_to(worktree).parts
    if ".soloscale" not in relative_parts:
        raise typer.BadParameter(
            "a data root inside a Git worktree must stay under a .soloscale directory",
            param_hint="--data-root",
        )
    if not _git_ignores_path(worktree, resolved_root):
        raise typer.BadParameter(
            "the selected .soloscale data root is not ignored by Git; add an ignore "
            "rule before storing private evidence",
            param_hint="--data-root",
        )


def _git_ignores_path(worktree: Path, data_root: Path) -> bool:
    git_executable = shutil.which("git")
    if git_executable is None:
        return False
    probe = data_root / ".casebook-private-probe"
    try:
        result = subprocess.run(
            [
                git_executable,
                "-C",
                str(worktree),
                "check-ignore",
                "--quiet",
                "--no-index",
                "--",
                str(probe),
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


@app.command("task-create")
def task_create(
    title: Annotated[str, typer.Option(help="Task title")],
    goal: Annotated[str, typer.Option(help="Desired outcome")],
    repo: Annotated[
        str | None, typer.Option(help="Local or GitHub repository reference")
    ] = None,
    branch: Annotated[str | None, typer.Option(help="Approved working branch")] = None,
    requested_path: Annotated[
        list[str] | None,
        typer.Option("--requested-path", help="In-scope path; repeat for multiple paths"),
    ] = None,
    constraint: Annotated[
        list[str] | None,
        typer.Option("--constraint", help="Task constraint; repeat for multiple constraints"),
    ] = None,
    frozen_decision: Annotated[
        list[str] | None,
        typer.Option(
            "--frozen-decision",
            help="Approved decision; repeat for multiple decisions",
        ),
    ] = None,
    required_change: Annotated[
        list[str] | None,
        typer.Option(
            "--required-change",
            help="Required implementation change; repeat for multiple changes",
        ),
    ] = None,
    acceptance_criterion: Annotated[
        list[str] | None,
        typer.Option(
            "--acceptance-criterion",
            help="Acceptance criterion; repeat for multiple criteria",
        ),
    ] = None,
    test_to_run: Annotated[
        list[str] | None,
        typer.Option("--test-to-run", help="Verification command; repeat for multiple tests"),
    ] = None,
    non_goal: Annotated[
        list[str] | None,
        typer.Option("--non-goal", help="Explicit non-goal; repeat for multiple non-goals"),
    ] = None,
    reasoning_depth: Annotated[ReasoningDepth, typer.Option()] = ReasoningDepth.MEDIUM,
    latency_tolerance: Annotated[LatencyTolerance, typer.Option()] = LatencyTolerance.BATCH,
    risk: Annotated[RiskLevel, typer.Option()] = RiskLevel.MEDIUM,
    requires_local: Annotated[
        bool, typer.Option(help="Requires local files or terminal")
    ] = False,
    requires_realtime: Annotated[bool, typer.Option()] = False,
    requires_scheduled: Annotated[bool, typer.Option()] = False,
    plugin: Annotated[
        str | None, typer.Option(help="Connected plugin that can complete the action")
    ] = None,
    public_action: Annotated[bool, typer.Option()] = False,
) -> None:
    task = TaskEnvelope(
        title=title,
        goal=goal,
        repository=repo,
        branch=branch,
        requested_paths=requested_path or [],
        constraints=constraint or [],
        frozen_decisions=frozen_decision or [],
        required_changes=required_change or [],
        acceptance_criteria=acceptance_criterion or [],
        tests_to_run=test_to_run or [],
        non_goals=non_goal or [],
        reasoning_depth=reasoning_depth,
        latency_tolerance=latency_tolerance,
        risk=risk,
        requires_local_files=requires_local,
        requires_terminal=requires_local,
        requires_realtime=requires_realtime,
        requires_scheduled_execution=requires_scheduled,
        plugin_can_complete=plugin is not None,
        plugin_name=plugin,
        public_action=public_action,
    )
    path = Path(".soloscale") / "tasks" / task.id / "task.json"
    _write_json(path, task.model_dump(mode="json"))
    console.print(f"[green]Created[/green] {path}")
    decision = route_task(task)
    console.print(Panel(decision.model_dump_json(indent=2), title="Route decision"))


@app.command("task-route")
def task_route(task_file: Annotated[Path, typer.Argument(help="Task Envelope JSON")]) -> None:
    task = TaskEnvelope.model_validate_json(task_file.read_text(encoding="utf-8"))
    decision = route_task(task)
    table = Table(title=f"Route — {task.id}")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("Primary", decision.primary.value)
    table.add_row("Secondary", ", ".join(item.value for item in decision.secondary) or "None")
    table.add_row("Human gate", str(decision.human_gate_required))
    table.add_row("Rationale", "\n".join(decision.rationale))
    console.print(table)


@app.command("packet-create")
def packet_create(task_file: Annotated[Path, typer.Argument(help="Task Envelope JSON")]) -> None:
    task = TaskEnvelope.model_validate_json(task_file.read_text(encoding="utf-8"))
    packet = packet_from_task(task)
    out = task_file.parent / "execution-packet.md"
    out.write_text(render_packet_markdown(packet), encoding="utf-8")
    console.print(f"[green]Created[/green] {out}")


@app.command("buildlog-export")
def buildlog_export(
    summary_file: Annotated[Path, typer.Argument(help="Run Summary JSON")],
) -> None:
    summary = RunSummary.model_validate_json(summary_file.read_text(encoding="utf-8"))
    payload = export_buildlog_iteration(summary)
    out = summary_file.parent / "buildlog-iteration.json"
    _write_json(out, payload)
    console.print(f"[green]Created[/green] {out}")


@app.command("case-create")
def case_create(
    title: Annotated[str, typer.Option(help="Engineering case title")],
    project: Annotated[str, typer.Option(help="Project or product name")],
    problem: Annotated[str, typer.Option(help="Observed engineering problem")],
    expected: Annotated[str, typer.Option(help="Expected behavior")],
    actual: Annotated[str, typer.Option(help="Actual behavior")],
    root_cause: Annotated[
        str,
        typer.Option("--root-cause", help="Confirmed cause or an explicit UNKNOWN statement"),
    ],
    resolution: Annotated[str, typer.Option(help="Resolution or bounded recovery decision")],
    evidence: Annotated[
        list[str] | None,
        typer.Option(
            "--evidence",
            help="Selected local evidence as KIND=PATH; repeat for multiple files",
        ),
    ] = None,
    verification: Annotated[
        list[str] | None,
        typer.Option(
            "--verification",
            help="Observed verification statement; repeat for multiple statements",
        ),
    ] = None,
    concept: Annotated[
        list[str] | None,
        typer.Option("--concept", help="Concept to master; repeat for multiple concepts"),
    ] = None,
    case_id: Annotated[
        str | None,
        typer.Option("--case-id", help="Stable lowercase-and-hyphen case identifier"),
    ] = None,
    repository: Annotated[
        str | None,
        typer.Option(help="Optional repository reference"),
    ] = None,
    alternative: Annotated[
        list[str] | None,
        typer.Option("--alternative", help="Alternative considered; repeat as needed"),
    ] = None,
    trade_off: Annotated[
        list[str] | None,
        typer.Option("--trade-off", help="Trade-off; repeat as needed"),
    ] = None,
    unknown: Annotated[
        list[str] | None,
        typer.Option("--unknown", help="Explicit unknown; repeat as needed"),
    ] = None,
    data_root: Annotated[
        Path,
        typer.Option("--data-root", help="Private SoloScale data root"),
    ] = Path(".soloscale"),
) -> None:
    if not verification:
        raise typer.BadParameter(
            "at least one verification statement is required",
            param_hint="--verification",
        )
    if not concept:
        raise typer.BadParameter(
            "at least one learning concept is required",
            param_hint="--concept",
        )
    sources = _parse_evidence_sources(evidence or [])
    _validate_private_data_root(data_root)
    try:
        store = CasebookStore(data_root)
        case = store.create_case(
            case_id=case_id,
            title=title,
            project=project,
            problem=problem,
            expected_behavior=expected,
            actual_behavior=actual,
            root_cause=root_cause,
            resolution=resolution,
            verification=verification,
            concepts=concept,
            evidence_sources=sources,
            repository=repository,
            alternatives_considered=alternative or [],
            trade_offs=trade_off or [],
            unknowns=unknown or [],
        )
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc

    case_dir = data_root / "cases" / case.id
    console.print(f"[green]Created case[/green] {case.id}")
    console.print(f"Case record: {case_dir / 'case.json'}")
    console.print(f"Interview packet: {case_dir / 'interview-packet.md'}")
    console.print("Current status: CAPTURED | Readiness: 0/5 | Next action: EXPLAIN")
    try:
        dashboard = build_control_tower(store)
    except Exception:
        console.print(
            "[yellow]Warning:[/yellow] The case is committed, but the Control Tower "
            "refresh failed; do not retry case-create."
        )
    else:
        console.print(f"Control Tower: {dashboard}")


@app.command("case-attempt")
def case_attempt(
    case: Annotated[str, typer.Argument(help="Case ID or path to case.json")],
    stage: Annotated[PracticeStage, typer.Option(help="Practice stage")],
    outcome: Annotated[AttemptOutcome, typer.Option(help="Self-assessed outcome")],
    receipt: Annotated[
        Path | None,
        typer.Option(help="Non-empty practice artifact; required for pass"),
    ] = None,
    note: Annotated[str | None, typer.Option(help="Attempt note")] = None,
    data_root: Annotated[
        Path,
        typer.Option("--data-root", help="Private SoloScale data root"),
    ] = Path(".soloscale"),
) -> None:
    case_id, resolved_root = _resolve_case_target(case, data_root)
    _validate_private_data_root(resolved_root)
    try:
        store = CasebookStore(resolved_root)
        result = store.record_attempt(
            case_id,
            stage,
            outcome,
            receipt_path=receipt,
            note=note,
        )
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    snapshot = result.mastery
    next_action = snapshot.next_stage.value.upper() if snapshot.next_stage else "COMPLETE"
    status = snapshot.status.value.replace("-", "_").upper()
    console.print(
        f"[green]Recorded[/green] {stage.value.upper()} → {outcome.value.upper()} "
        "[green]— attempt committed[/green]"
    )
    console.print(
        f"Current status: {status} | Readiness: {len(snapshot.passed_stages)}/5 | "
        f"Next action: {next_action}"
    )
    if result.commit_warning is not None:
        console.print(
            "[yellow]Warning:[/yellow] The attempt reached durable commit, but closing "
            "the attempt log reported an error; do not retry case-attempt. Inspect "
            "case-status and evidence integrity."
        )
    if not result.packet_refreshed:
        console.print(
            "[yellow]Warning:[/yellow] The attempt is committed, but the interview "
            "packet refresh failed; do not retry case-attempt."
        )
    try:
        dashboard = build_control_tower(store)
    except Exception:
        console.print(
            "[yellow]Warning:[/yellow] The attempt is committed, but the Control Tower "
            "refresh failed; do not retry case-attempt."
        )
    else:
        console.print(f"Control Tower: {dashboard}")


@app.command("case-status")
def case_status(
    case: Annotated[str, typer.Argument(help="Case ID or path to case.json")],
    data_root: Annotated[
        Path,
        typer.Option("--data-root", help="Private SoloScale data root"),
    ] = Path(".soloscale"),
) -> None:
    case_id, resolved_root = _resolve_case_target(case, data_root)
    _validate_private_data_root(resolved_root)
    try:
        store = CasebookStore(resolved_root)
        learning_case = store.load_case(case_id)
        snapshot = store.mastery(case_id)
        integrity = store.verify_integrity(case_id)
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc

    status = snapshot.status.value.replace("-", "_").upper()
    next_action = snapshot.next_stage.value.upper() if snapshot.next_stage else "COMPLETE"
    summary = Table(title=f"Case — {learning_case.title}")
    summary.add_column("Track")
    summary.add_column("Current state")
    summary.add_row("Engineering", learning_case.engineering_state.value.upper())
    summary.add_row("Evidence integrity", "PASS" if integrity.ok else "FAIL")
    summary.add_row("Learning", status)
    summary.add_row("Readiness", f"{len(snapshot.passed_stages)}/5")
    summary.add_row("Next action", next_action)
    console.print(summary)

    stages = Table(title="Practice gates")
    stages.add_column("Stage")
    stages.add_column("Latest result")
    for practice_stage in PracticeStage:
        result = snapshot.stage_results[practice_stage]
        label = "NOT STARTED" if result is None else result.value.replace("-", " ").upper()
        stages.add_row(practice_stage.value.upper(), label)
    console.print(stages)


@app.command("control-tower-build")
def control_tower_build(
    data_root: Annotated[
        Path,
        typer.Option("--data-root", help="Private SoloScale data root"),
    ] = Path(".soloscale"),
    output: Annotated[
        Path | None,
        typer.Option(help="Optional output HTML path"),
    ] = None,
) -> None:
    _validate_private_data_root(data_root)
    try:
        store = CasebookStore(data_root)
        out = build_control_tower(store, output)
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc), param_hint="--output") from exc
    console.print(f"[green]Created[/green] {out}")


@app.command("demo")
def demo() -> None:
    task = TaskEnvelope(
        id="task-research-citations-001",
        title="Add source-grounded citations to the Research Agent",
        goal=(
            "Every externally verifiable claim in the generated report must link to "
            "inspectable source evidence."
        ),
        repository="../AI-Research-Assistant-LangJu-Edition",
        branch="feat/source-grounded-citations",
        requested_paths=["app/", "tests/"],
        reasoning_depth=ReasoningDepth.HIGH,
        requires_local_files=True,
        requires_terminal=True,
        frozen_decisions=["Missing source evidence must never be invented."],
        required_changes=["Trace every externally verifiable claim to a source identifier."],
        constraints=[
            "Preserve the existing user-facing workflow.",
            "Do not add a production dependency without approval.",
        ],
        acceptance_criteria=[
            "Each report claim can be traced to a source identifier.",
            "Missing sources are represented honestly rather than invented.",
            "Unit tests cover successful and missing-citation paths.",
        ],
        tests_to_run=["pytest"],
        non_goals=[
            "Redesigning the entire Research Agent UI.",
            "Adding autonomous publishing.",
        ],
    )
    decision = route_task(task)
    packet = packet_from_task(task)
    console.print(Panel(task.model_dump_json(indent=2), title="Task Envelope"))
    console.print(Panel(decision.model_dump_json(indent=2), title="Route Decision"))
    console.print(Panel(render_packet_markdown(packet), title="Execution Packet"))


if __name__ == "__main__":
    app()
