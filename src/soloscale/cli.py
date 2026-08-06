from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from soloscale.buildlog_adapter import export_buildlog_iteration
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
