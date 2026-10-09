#!/usr/bin/env python3
"""Run the evaluation suite and write the actual results (plan.md section 10).

This executes the ground-truth scenario suite through the real engine, then writes:

* ``docs/experiments/results-<stamp>.json``   — the full summary with every metric
* ``docs/experiments/runs-<stamp>.csv``       — one row per stored run record
* ``docs/experiments/report-<stamp>.md``      — the Markdown results report
* ``docs/experiments/latest.*``               — copies of the above for the docs

Nothing here is fabricated: every number is computed from run records the engine
produced in this invocation, and the printed summary is the source of the numbers
quoted in the project report.

Usage:
    python3 scripts/run_evaluation.py                      # default suite
    python3 scripts/run_evaluation.py --runs-per-scenario 10
    python3 scripts/run_evaluation.py --seed 20261009 --runs-per-scenario 30
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import MODEL_VERSION, PRIOR_CONFIG_VERSION  # noqa: E402
from app.experiments.exports import (  # noqa: E402
    experiment_runs_csv,
    experiment_summary_markdown,
)
from app.experiments.metrics import scenario_variation  # noqa: E402
from app.experiments.runner import (  # noqa: E402
    ExperimentConfig,
    plan_experiment,
    run_experiment,
)
from app.storage.database import Database  # noqa: E402

DEFAULT_SEED = 20261009


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs-per-scenario", type=int, default=10)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--max-probes", type=int, default=8)
    parser.add_argument(
        "--stamp",
        default=datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"),
        help="filename suffix; defaults to the current UTC time",
    )
    parser.add_argument(
        "--keep-database",
        action="store_true",
        help="keep the SQLite file used for the run instead of using a temporary one",
    )
    args = parser.parse_args()

    out_dir = ROOT / "docs" / "experiments"
    out_dir.mkdir(parents=True, exist_ok=True)
    db_path = out_dir / f"evaluation-{args.stamp}.sqlite3"

    config = ExperimentConfig(
        runs_per_scenario=args.runs_per_scenario,
        seed=args.seed,
        strategies=["adaptive", "baseline"],
        max_probes=args.max_probes,
        include_control=True,
    )
    plan = plan_experiment(config)
    print("NetSleuth evaluation suite")
    print(f"  model          {MODEL_VERSION} / {PRIOR_CONFIG_VERSION}")
    print(f"  seed           {args.seed} (each run uses seed + run_index)")
    print(f"  runs/scenario  {args.runs_per_scenario}")
    print(f"  probe budget   {args.max_probes}")
    print(f"  planned runs   {plan['total_runs']}")
    print()

    database = Database(db_path)
    started = time.time()
    summary = run_experiment(
        database,
        config,
        name=f"NetSleuth fault suite (seed {args.seed}, {args.runs_per_scenario} runs/scenario)",
    )
    wall = time.time() - started

    runs = database.list_experiment_runs(summary["experiment_id"])
    variation = scenario_variation(runs)
    summary["variation"] = variation
    summary["database"] = str(db_path)
    summary["python_wall_clock_s"] = round(wall, 3)

    json_path = out_dir / f"results-{args.stamp}.json"
    csv_path = out_dir / f"runs-{args.stamp}.csv"
    md_path = out_dir / f"report-{args.stamp}.md"

    json_path.write_text(json.dumps(summary, indent=2, sort_keys=False))
    csv_path.write_text(experiment_runs_csv(runs, experiment_id=summary["experiment_id"]))
    md_path.write_text(experiment_summary_markdown(summary))

    # Stable "latest" copies so the docs can link to a fixed path. These are real
    # outputs of this run, not hand-written numbers.
    for source, name in ((json_path, "latest.json"), (csv_path, "latest.csv"), (md_path, "latest.md")):
        shutil.copyfile(source, out_dir / name)

    metrics = summary["metrics"]
    print(f"Completed {summary['completed_runs']} runs in {wall:.2f} s "
          f"(engine-reported: {summary['wall_clock_ms']:.0f} ms)")
    print()
    header = f"{'strategy':10s} {'runs':>5s} {'top-1':>7s} {'top-3':>7s} " \
             f"{'meanprobe':>10s} {'coverage':>9s} {'inconcl':>8s}"
    print(header)
    print("-" * len(header))
    for strategy, block in metrics["by_strategy"].items():
        print(
            f"{strategy:10s} {block['runs']:5d} "
            f"{block['top1_accuracy'] * 100:6.1f}% {block['top3_accuracy'] * 100:6.1f}% "
            f"{block['mean_probes_all']:10.2f} "
            f"{block['coverage'] * 100:8.1f}% {block['inconclusive_rate'] * 100:7.1f}%"
        )
    print()
    print("Per fault class (adaptive top-1):")
    for label, block in sorted(metrics["by_fault_class"].items()):
        adaptive = block["by_strategy"].get("adaptive")
        baseline = block["by_strategy"].get("baseline")
        if not adaptive:
            continue
        adaptive_rate = (adaptive["top1_accuracy"] or 0) * 100
        baseline_rate = (baseline["top1_accuracy"] or 0) * 100 if baseline else 0.0
        print(
            f"  {label:32s} adaptive {adaptive_rate:6.1f}% "
            f"({adaptive['top1_correct']:3d}/{adaptive['runs']:3d})   "
            f"baseline {baseline_rate:6.1f}% "
            f"({baseline['top1_correct'] if baseline else 0:3d}/"
            f"{baseline['runs'] if baseline else 0:3d})"
        )
    print()
    print("Wrote:")
    print(f"  {json_path.relative_to(ROOT)}")
    print(f"  {csv_path.relative_to(ROOT)}")
    print(f"  {md_path.relative_to(ROOT)}")
    print(f"  {out_dir.relative_to(ROOT)}/latest.{{json,csv,md}}")
    if not args.keep_database:
        # The CSV/JSON already contain everything the report needs; dropping the
        # database keeps the repository small. Pass --keep-database to inspect it.
        database.close()
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(f"{db_path}{suffix}")
            if candidate.exists():
                candidate.unlink()
        print("  (temporary SQLite database removed; use --keep-database to keep it)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
