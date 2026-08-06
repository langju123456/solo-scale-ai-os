from __future__ import annotations

import json
from pathlib import Path

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
    title: str = typer.Option(..., help="Task title"),
    goal: str = typer.Option(..., help="Desired outcome"),
    repo: str | None = typer.Option(None, help="Local or GitHub repository reference"),
    reasoning_depth: ReasoningDepth = typer.Option(ReasoningDepth.MEDIUM),
    latency_tolerance: LatencyTolerance = typer.Option(LatencyTolerance.BATCH),
    risk: RiskLevel = typer.Option(RiskLevel.MEDIUM),
    requires_local: bool = typer.Option(False, help="Requires local files or terminal"),
    requires_realtime: bool = typer.Option(False),
    requires_scheduled: bool = typer.Option(False),
    plugin: str | None = typer.Option(None, help="Connected plugin that can complete the action"),
    public_action: bool = typer.Option(False),
) -> None:
    task = TaskEnvelope(
        title=title,
        goal=goal,
        repository=repo,
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
def task_route(task_file: Path) -> None:
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
def packet_create(task_file: Path) -> None:
    task = TaskEnvelope.model_validate_json(task_file.read_text(encoding="utf-8"))
    packet = packet_from_task(task)
    out = task_file.parent / "execution-packet.md"
    out.write_text(render_packet_markdown(packet), encoding="utf-8")
    console.print(f"[green]Created[/green] {out}")


@app.command("buildlog-export")
def buildlog_export(summary_file: Path) -> None:
    summary = RunSummary.model_validate_json(summary_file.read_text(encoding="utf-8"))
    payload = export_buildlog_iteration(summary)
    out = summary_file.parent / "buildlog-iteration.json"
    _write_json(out, payload)
    console.print(f"[green]Created[/green] {out}")


@app.command("demo")
def demo() -> None:
    example = Path("examples/research_agent_task.json")
    task = TaskEnvelope.model_validate_json(example.read_text(encoding="utf-8"))
    decision = route_task(task)
    packet = packet_from_task(task)
    console.print(Panel(task.model_dump_json(indent=2), title="Task Envelope"))
    console.print(Panel(decision.model_dump_json(indent=2), title="Route Decision"))
    console.print(Panel(render_packet_markdown(packet), title="Execution Packet"))


if __name__ == "__main__":
    app()
