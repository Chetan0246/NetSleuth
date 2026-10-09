"""Report and experiment export (Markdown, JSON, CSV).

Every export is generated from stored records — the diagnosis report from the
persisted observations, the experiment export from the persisted run rows — so an
exported file can always be traced back to what actually executed.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from ..core.config import MODEL_VERSION, PRIOR_CONFIG_VERSION, APP_VERSION


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _pct(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value) * 100:.1f}%"


# ---------------------------------------------------------------------------
# single-diagnosis report
# ---------------------------------------------------------------------------


def diagnosis_report_markdown(payload: dict[str, Any]) -> str:
    """Render one diagnosis as a Markdown report from its stored result."""
    beliefs = payload.get("beliefs", {})
    ranked = beliefs.get("ranked", [])
    report = payload.get("report", {})
    lines: list[str] = []
    lines.append(f"# NetSleuth diagnostic report — {payload['id']}")
    lines.append("")
    lines.append(f"**Mode:** `{payload.get('mode', 'SIMULATED LAB')}`")
    lines.append(f"**Strategy:** {payload.get('strategy')} ({payload.get('strategy_label', '')})")
    lines.append(f"**Status:** `{payload.get('status')}`")
    lines.append(f"**Source:** `{payload.get('source_node_id')}`")
    lines.append(
        f"**Destination:** `{payload.get('destination_node_id')}`"
        + (f" service `{payload.get('destination_service')}`" if payload.get("destination_service") else "")
        + (f" port `{payload.get('port')}`" if payload.get("port") else "")
    )
    lines.append(
        f"**Probes executed:** {payload.get('probes_used')} / {payload.get('max_probes')}"
    )
    lines.append(f"**Belief entropy:** {_fmt(beliefs.get('entropy_bits'))} bits")
    lines.append(f"**Random seed:** {payload.get('random_seed')}")
    lines.append(f"**Started:** {payload.get('started_at')}  ")
    lines.append(f"**Completed:** {payload.get('completed_at') or 'still running'}")
    lines.append("")
    lines.append("## Conclusion")
    lines.append("")
    lines.append(f"**{report.get('headline', 'n/a')}**")
    lines.append("")
    lines.append(report.get("summary", ""))
    lines.append("")
    lines.append(f"**Uncertainty:** {report.get('uncertainty_note', '')}")
    lines.append("")
    lines.append(f"**Stopping rule:** {payload.get('stopping_reason', '')}")
    lines.append("")

    component = payload.get("suspected_component") or {}
    lines.append("## Suspected component")
    lines.append("")
    if component.get("component_id"):
        lines.append(
            f"- Component: `{component.get('component_kind')} {component.get('component_id')}`"
        )
        lines.append(f"- Location confidence: `{component.get('confidence')}`")
        for item in component.get("evidence", []):
            lines.append(f"- {item}")
    else:
        lines.append("- No single component could be identified from the executed probes.")
        for item in component.get("evidence", []):
            lines.append(f"- {item}")
    lines.append("")

    lines.append("## Hypothesis ranking")
    lines.append("")
    lines.append("| Rank | Hypothesis | Posterior | Prior | Layers |")
    lines.append("|---:|---|---:|---:|---|")
    for index, item in enumerate(ranked, start=1):
        layers = ", ".join(item.get("layers", []))
        lines.append(
            f"| {index} | `{item.get('code')}` — {item.get('title', '')} | "
            f"{_pct(item.get('probability'))} | {_pct(item.get('prior'))} | {layers} |"
        )
    lines.append("")

    lines.append("## Evidence supporting the leading hypothesis")
    lines.append("")
    supporting = report.get("supporting_evidence") or []
    lines.extend(f"- {item}" for item in supporting) if supporting else lines.append(
        "- No probe produced evidence that specifically raises the leading hypothesis."
    )
    lines.append("")
    lines.append("## Evidence weakening the leading hypothesis")
    lines.append("")
    weakening = report.get("weakening_evidence") or []
    lines.extend(f"- {item}" for item in weakening) if weakening else lines.append(
        "- No executed probe weakened the leading hypothesis."
    )
    lines.append("")
    lines.append("## Not explained by the leading hypothesis")
    lines.append("")
    unexplained = report.get("unexplained") or []
    lines.extend(f"- {item}" for item in unexplained) if unexplained else lines.append(
        "- The executed evidence is consistent with the leading hypothesis."
    )
    lines.append("")

    lines.append("## Probe timeline")
    lines.append("")
    lines.append(
        "| # | Probe | Outcome | Modelled ms | Measured ms | Info gained (bits) | Why it was chosen |"
    )
    lines.append("|---:|---|---|---:|---:|---:|---|")
    contributions = {item["probe_key"] + str(item["sequence_number"]): item
                     for item in report.get("contributions", [])}
    for step in payload.get("steps", []):
        key = step["probe_key"] + str(step["sequence_number"])
        contribution = contributions.get(key, {})
        lines.append(
            f"| {step['sequence_number']} | {step['probe_label']} (`{step['probe_key']}`) | "
            f"`{step['outcome']}` | {_fmt(step.get('modelled_elapsed_ms'))} | "
            f"{_fmt(step.get('measured_wall_clock_ms'))} | "
            f"{_fmt(contribution.get('information_gained_bits'))} | "
            f"{step.get('selected_reason', '')} |"
        )
    lines.append("")

    lines.append("## Evidence detail")
    lines.append("")
    for step in payload.get("steps", []):
        lines.append(f"### {step['sequence_number']}. {step['probe_label']}")
        lines.append("")
        lines.append(f"- Outcome: `{step['outcome']}`")
        lines.append(f"- Summary: {step['summary']}")
        lines.append(f"- Entropy: {_fmt(step.get('entropy_before_bits'))} -> "
                     f"{_fmt(step.get('entropy_after_bits'))} bits")
        for statement in step.get("evidence", []):
            lines.append(f"- [{statement.get('kind', 'reading')}] {statement.get('statement', '')}")
        lines.append("")

    lines.append("## Recommended next step")
    lines.append("")
    lines.append(report.get("recommended_next_step", ""))
    lines.append("")
    if report.get("remediation"):
        lines.append("**Remediation options documented for this hypothesis:**")
        lines.append("")
        lines.extend(f"- {item}" for item in report["remediation"])
        lines.append("")

    lines.append("## Caveats and provenance")
    lines.append("")
    lines.extend(f"- {item}" for item in report.get("caveats", []))
    lines.append(
        f"- Exported from NetSleuth {APP_VERSION}; model `{MODEL_VERSION}`; priors "
        f"`{PRIOR_CONFIG_VERSION}`."
    )
    lines.append("")
    return "\n".join(lines)


def diagnosis_report_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=False)


# ---------------------------------------------------------------------------
# experiment export
# ---------------------------------------------------------------------------

RUN_CSV_COLUMNS = [
    "experiment_id",
    "id",
    "scenario_id",
    "template_id",
    "strategy",
    "seed",
    "source_node_id",
    "destination_node_id",
    "destination_service",
    "actual_fault_type",
    "actual_target_id",
    "predicted_fault_type",
    "predicted_probability",
    "predicted_target_id",
    "localization_confidence",
    "is_correct_top1",
    "is_correct_top3",
    "is_localization_correct",
    "is_inconclusive",
    "status",
    "probes_used",
    "elapsed_ms",
    "created_at",
]


def experiment_runs_csv(
    runs: list[dict[str, Any]], *, experiment_id: str
) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=RUN_CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for run in runs:
        row = dict(run)
        row["experiment_id"] = experiment_id
        writer.writerow(row)
    return buffer.getvalue()


def experiment_summary_markdown(summary: dict[str, Any]) -> str:
    """Render an experiment summary as Markdown, from the stored metrics."""
    config = summary.get("config", {})
    metrics = summary.get("metrics", {})
    lines: list[str] = []
    lines.append(f"# NetSleuth experiment report — {summary.get('experiment_id')}")
    lines.append("")
    lines.append(f"**Name:** {summary.get('name')}")
    lines.append(f"**Generated:** {summary.get('generated_at')}")
    lines.append(f"**Completed runs:** {summary.get('completed_runs')}")
    lines.append(
        f"**Measured experiment wall-clock time:** {_fmt(summary.get('wall_clock_ms'))} ms "
        "(this is simulator computation time, not network latency)"
    )
    lines.append("")
    lines.append("## Configuration (reproducibility)")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(config, indent=2, sort_keys=True))
    lines.append("```")
    lines.append("")
    lines.append("## Headline metrics by strategy")
    lines.append("")
    lines.append(
        "| Strategy | Runs | Top-1 | Top-3 | Top-1 (accepted confusions) | Mean probes | "
        "Mean probes (conclusive) | Coverage | Inconclusive | Mean elapsed ms | Localization |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for strategy, block in (metrics.get("by_strategy") or {}).items():
        lines.append(
            f"| `{strategy}` | {block.get('runs')} | {_pct(block.get('top1_accuracy'))} | "
            f"{_pct(block.get('top3_accuracy'))} | "
            f"{_pct(block.get('accepted_confusion_adjusted_accuracy'))} | "
            f"{_fmt(block.get('mean_probes_all'))} | {_fmt(block.get('mean_probes_conclusive'))} | "
            f"{_pct(block.get('coverage'))} | {_pct(block.get('inconclusive_rate'))} | "
            f"{_fmt(block.get('mean_elapsed_ms'))} | {_pct(block.get('localization_accuracy'))} |"
        )
    lines.append("")
    lines.append("## Per-fault-class breakdown")
    lines.append("")
    lines.append("| Expected cause | Strategy | Runs | Top-1 | Top-3 | Mean probes | Coverage |")
    lines.append("|---|---|---:|---:|---:|---:|---:|")
    for hypothesis, block in (metrics.get("by_fault_class") or {}).items():
        for strategy, strat_block in block["by_strategy"].items():
            lines.append(
                f"| `{hypothesis}` | `{strategy}` | {strat_block.get('runs')} | "
                f"{_pct(strat_block.get('top1_accuracy'))} | "
                f"{_pct(strat_block.get('top3_accuracy'))} | "
                f"{_fmt(strat_block.get('mean_probes_all'))} | "
                f"{_pct(strat_block.get('coverage'))} |"
            )
    lines.append("")
    matrix = metrics.get("confusion_matrix") or {}
    labels = matrix.get("labels") or []
    if labels:
        lines.append("## Confusion matrix (rows = actual injected fault, columns = predicted)")
        lines.append("")
        lines.append("| Actual \\ Predicted | " + " | ".join(f"`{label}`" for label in labels) + " |")
        lines.append("|---" * (len(labels) + 1) + "|")
        for actual in labels:
            row = matrix["matrix"].get(actual, {})
            lines.append(
                f"| `{actual}` | " + " | ".join(str(row.get(label, 0)) for label in labels) + " |"
            )
        lines.append("")
    lines.append("## Per-scenario results")
    lines.append("")
    lines.append("| Scenario | Fault | Strategy | Runs | Top-1 | Mean probes | Inconclusive |")
    lines.append("|---|---|---|---:|---:|---:|---:|")
    for item in metrics.get("per_scenario") or []:
        for strategy, block in item["by_strategy"].items():
            lines.append(
                f"| `{item['scenario_id']}` | `{item['actual_fault_type'] or 'none'}` | "
                f"`{strategy}` | {block.get('runs')} | {_pct(block.get('top1_accuracy'))} | "
                f"{_fmt(block.get('mean_probes_all'))} | {_pct(block.get('inconclusive_rate'))} |"
            )
    lines.append("")
    lines.append("## Methodology notes")
    lines.append("")
    lines.extend(f"- {item}" for item in metrics.get("notes", []))
    lines.append(
        f"- Model `{MODEL_VERSION}`; priors `{PRIOR_CONFIG_VERSION}`. Ground-truth fault labels "
        "are read only by the evaluator, after the run."
    )
    lines.append("")
    return "\n".join(lines)


def experiment_summary_json(summary: dict[str, Any]) -> str:
    return json.dumps(summary, indent=2, sort_keys=False)


__all__ = [
    "RUN_CSV_COLUMNS",
    "diagnosis_report_json",
    "diagnosis_report_markdown",
    "experiment_runs_csv",
    "experiment_summary_json",
    "experiment_summary_markdown",
]
