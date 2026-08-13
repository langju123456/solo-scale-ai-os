"""Metadata-only Evidence Center rendering and explicit local refresh helpers."""

from __future__ import annotations

import html
from collections.abc import Sequence
from pathlib import Path

from soloscale.evidence_hub import EvidenceHub
from soloscale.evidence_hub_models import EvidenceHubStatus, SyncReceipt
from soloscale.knowledge_store import KnowledgeStore


def refresh_evidence_catalog(
    data_root: Path,
    *,
    repository_root: Path,
    buildlog_roots: Sequence[Path] = (),
) -> SyncReceipt:
    """Explicitly build the evidence catalog without models, network, or publishing."""

    root = Path(data_root)
    knowledge_path = root / "knowledge" / "index.sqlite3"
    knowledge_store = KnowledgeStore(root) if knowledge_path.is_file() else None
    selected_buildlog_roots = list(buildlog_roots)
    discovered_buildlog = root / "publishing"
    if discovered_buildlog.is_dir() and discovered_buildlog not in selected_buildlog_roots:
        selected_buildlog_roots.append(discovered_buildlog)
    return EvidenceHub(root).refresh(
        knowledge_store=knowledge_store,
        buildlog_roots=selected_buildlog_roots,
        git_root=repository_root,
    )


def evidence_page(data_root: Path) -> str:
    """Render a catalog view without creating or exposing private catalog contents."""

    root = Path(data_root)
    exists = EvidenceHub.catalog_exists(root)
    hub = EvidenceHub(root) if exists else None
    status = hub.status() if hub else None
    receipts = hub.recent_receipts(limit=5) if hub else []
    assets = hub.recent_assets(limit=5) if hub else []
    outcomes = hub.recent_outcomes(limit=5) if hub else []
    receipt_items = [
        f"{receipt.receipt_id} · {receipt.status} · {receipt.adapter} · "
        f"created {receipt.created_count} · updated {receipt.updated_count} · "
        f"unchanged {receipt.unchanged_count} · errors {receipt.error_count}"
        for receipt in receipts
    ]
    asset_items = [f"{asset.asset_id} · {asset.asset_type} · {asset.approval}" for asset in assets]
    outcome_items = [
        f"{outcome.outcome_id} · {outcome.outcome_type} · {outcome.platform} · {outcome.status}"
        for outcome in outcomes
    ]
    return f"""<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">
<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
<title>SoloScale · Evidence Center</title>
<style>
body{{margin:0;background:#07111f;color:#e5edf7;
font-family:Inter,-apple-system,BlinkMacSystemFont,\"Segoe UI\",sans-serif}}
main{{max-width:980px;margin:auto;padding:36px 24px}}a{{color:#93c5fd}}
.panel{{margin-top:18px;padding:20px;border:1px solid #334155;
border-radius:16px;background:#111827}}
.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}
.metric{{padding:14px;border:1px solid #334155;border-radius:12px;background:#0f172a}}
small,p,li{{color:#b8c4d6;line-height:1.55}}button{{padding:10px 14px;border:0;
border-radius:8px;background:#2563eb;color:#fff;font-weight:700}}
@media(max-width:700px){{.grid{{grid-template-columns:1fr}}}}
</style></head><body><main><nav><a href=\"/\">Resume</a> · <a href=\"/content\">Content Studio</a> ·
<a href=\"/publishing\">Publishing</a></nav>
<section class=\"panel\"><h1>Evidence Center</h1>
<p>Private catalog metadata only. This page never displays source bodies, locators,
paths, or credentials.</p>
<p><strong>Configured adapters:</strong> KnowledgeStore metadata (when indexed),
Git snapshot metadata,
BuildLog metadata from the private publishing namespace (when available), and
existing SoloScale application-run metadata.</p>
<form method=\"post\" action=\"/evidence/refresh\">
<button type=\"submit\">Refresh evidence catalog</button></form>
</section>
{_status_html(exists, status)}
<section class=\"panel\"><h2>Recent refresh receipts</h2>{_list_html(receipt_items)}</section>
<section class=\"panel\"><h2>Recent assets</h2>{_list_html(asset_items)}</section>
<section class=\"panel\"><h2>Recent outcomes</h2>{_list_html(outcome_items)}</section>
</main></body></html>"""


def _status_html(exists: bool, status: EvidenceHubStatus | None) -> str:
    if not exists or status is None:
        return (
            "<section class=\"panel\"><h2>Catalog status</h2><p>Not initialized.</p>"
            "<p><strong>Next action:</strong> Refresh evidence catalog.</p></section>"
        )
    counts = [
        ("Sources", status.source_count),
        ("Evidence", status.evidence_count),
        ("Bundles", status.bundle_count),
        ("Cases", status.case_count),
        ("Assets", status.asset_count),
        ("Outcomes", status.outcome_count),
    ]
    truth_counts = "".join(
        f"<li>{html.escape(key)}: {value}</li>"
        for key, value in sorted(status.truth_class_counts.items())
    ) or "<li>None</li>"
    source_counts = "".join(
        f"<li>{html.escape(key)}: {value}</li>"
        for key, value in sorted(status.source_counts.items())
    ) or "<li>None</li>"
    last_refresh = status.last_receipt.completed_at.isoformat() if status.last_receipt else "Never"
    next_action = (
        "Review the failed refresh, then refresh evidence catalog."
        if status.last_receipt and status.last_receipt.status.value == "failed"
        else "Refresh evidence catalog after adding sources."
        if status.source_count == 0
        else "Review evidence metadata before creating a bundle."
    )
    metrics = "".join(
        f"<div class=\"metric\"><small>{label}</small><br><strong>{value}</strong></div>"
        for label, value in counts
    )
    return f"""<section class=\"panel\"><h2>Catalog status</h2>
<p>Last refresh: {html.escape(last_refresh)}</p><div class=\"grid\">{metrics}</div>
<h3>Source types</h3><ul>{source_counts}</ul>
<h3>Truth classes</h3><ul>{truth_counts}</ul>
<p><strong>Next action:</strong> {html.escape(next_action)}</p></section>"""


def _list_html(items: list[str]) -> str:
    if not items:
        return "<p>None recorded.</p>"
    return "<ul>" + "".join(f"<li>{html.escape(item)}</li>" for item in items) + "</ul>"
