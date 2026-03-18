"""RAG evaluation runner — runs the full pipeline against labeled samples.

Usage:
    python -m src.evaluation.runner                    # run all 50 samples
    python -m src.evaluation.runner --samples 10       # first 10 samples
    python -m src.evaluation.runner --category access_control
    python -m src.evaluation.runner --difficulty hard
    python -m src.evaluation.runner --ids 1,5,17       # specific sample IDs
    python -m src.evaluation.runner --output results/eval_run.json
    python -m src.evaluation.runner --dry-run           # validate dataset only
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from src.config.settings import get_settings
from src.evaluation.dataset import (
    EvalSample,
    get_dataset,
    get_dataset_by_category,
    get_dataset_by_difficulty,
    dataset_summary,
)
from src.evaluation.metrics import (
    AggregateMetrics,
    SampleResult,
    compute_aggregate_metrics,
    compute_sample_metrics,
)
from src.retrieval.models import QueryRequest, QueryResponse
from src.retrieval.pipeline import query_finding

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("evaluation.runner")


# ── Run single sample ───────────────────────────────────────────────────────


def run_sample(sample: EvalSample) -> SampleResult:
    """Execute the pipeline for a single evaluation sample."""
    settings = get_settings()
    request = QueryRequest(
        finding_text=sample.finding_text,
        target_frameworks=sample.target_frameworks,
    )
    try:
        response = query_finding(request, settings=settings)
        return compute_sample_metrics(sample, response)
    except Exception as exc:
        logger.error("Sample %d failed: %s", sample.id, exc)
        return compute_sample_metrics(sample, None, error=str(exc))


# ── Report formatting ───────────────────────────────────────────────────────


def format_sample_report(result: SampleResult) -> str:
    """Format a single sample result as a readable string."""
    lines = [
        f"  Sample {result.sample_id} [{result.difficulty}|{result.category}]",
    ]
    if result.error:
        lines.append(f"    ERROR: {result.error}")
        return "\n".join(lines)

    lines.extend([
        f"    Controls — P: {result.control_precision:.2f}  "
        f"R: {result.control_recall:.2f}  F1: {result.control_f1:.2f}",
        f"    Expected:  {result.expected_controls}",
        f"    Predicted: {result.predicted_controls}",
        f"    Domain recall: {result.domain_recall:.2f}  "
        f"Approved: {result.approved_count}/{result.mappings_returned}  "
        f"Avg confidence: {result.avg_confidence:.0f}",
        f"    Chunks: {result.chunks_retrieved} retrieved → "
        f"{result.chunks_after_rerank} after rerank",
        f"    Time: {result.duration_seconds:.1f}s  Tokens: {result.total_tokens}",
    ])
    return "\n".join(lines)


def format_aggregate_report(agg: AggregateMetrics) -> str:
    """Format aggregate metrics as a readable report."""
    lines = [
        "",
        "=" * 72,
        "  RAG EVALUATION REPORT",
        "=" * 72,
        "",
        f"  Samples: {agg.successful_samples}/{agg.total_samples} successful"
        f"  ({agg.failed_samples} errors)",
        "",
        "  ── Control Mapping Quality ──────────────────────────────",
        f"    Mean Precision:  {agg.mean_control_precision:.3f}",
        f"    Mean Recall:     {agg.mean_control_recall:.3f}",
        f"    Mean F1:         {agg.mean_control_f1:.3f}",
        f"    Mean Domain Rec: {agg.mean_domain_recall:.3f}",
        "",
        "  ── Generation Quality ──────────────────────────────────",
        f"    Mean Approval Rate:     {agg.mean_approval_rate:.3f}",
        f"    Mean Avg Confidence:    {agg.mean_avg_confidence:.1f}",
        "",
        "  ── Retrieval Stats ─────────────────────────────────────",
        f"    Mean Chunks Retrieved:  {agg.mean_chunks_retrieved:.1f}",
        f"    Mean After Rerank:      {agg.mean_chunks_after_rerank:.1f}",
        "",
        "  ── Cost & Latency ──────────────────────────────────────",
        f"    Mean Latency:           {agg.mean_duration_seconds:.2f}s",
        f"    Total Tokens:           {agg.total_tokens:,}",
        f"    Mean Tokens/Query:      {agg.mean_tokens_per_query:.0f}",
    ]

    if agg.metrics_by_difficulty:
        lines.extend(["", "  ── By Difficulty ───────────────────────────────────────"])
        for d, m in sorted(agg.metrics_by_difficulty.items()):
            lines.append(
                f"    {d:8s}  n={m['count']:2d}  "
                f"P={m.get('mean_control_precision', 0):.3f}  "
                f"R={m.get('mean_control_recall', 0):.3f}  "
                f"F1={m.get('mean_control_f1', 0):.3f}"
            )

    if agg.metrics_by_category:
        lines.extend(["", "  ── By Category ─────────────────────────────────────────"])
        for c, m in sorted(agg.metrics_by_category.items()):
            lines.append(
                f"    {c:24s}  n={m['count']:2d}  "
                f"P={m.get('mean_control_precision', 0):.3f}  "
                f"R={m.get('mean_control_recall', 0):.3f}  "
                f"F1={m.get('mean_control_f1', 0):.3f}"
            )

    lines.extend(["", "=" * 72])
    return "\n".join(lines)


# ── Serialization ───────────────────────────────────────────────────────────


def _serialize_results(
    results: list[SampleResult],
    agg: AggregateMetrics,
) -> dict:
    """Serialize results + aggregate metrics to a JSON-safe dict."""
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "summary": asdict(agg),
        "samples": [asdict(r) for r in results],
    }


# ── CLI entry point ─────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Run RAG evaluation benchmark")
    parser.add_argument(
        "--samples", type=int, default=None,
        help="Run first N samples (default: all)",
    )
    parser.add_argument(
        "--category", type=str, default=None,
        help="Filter by category (e.g. access_control, encryption)",
    )
    parser.add_argument(
        "--difficulty", type=str, default=None,
        choices=["easy", "medium", "hard"],
        help="Filter by difficulty",
    )
    parser.add_argument(
        "--ids", type=str, default=None,
        help="Comma-separated sample IDs to run (e.g. 1,5,17)",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Path to write JSON results (default: stdout only)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Validate dataset and print summary without running pipeline",
    )
    args = parser.parse_args()

    # ── Dataset selection ────────────────────────────────
    if args.category:
        dataset = get_dataset_by_category(args.category)
    elif args.difficulty:
        dataset = get_dataset_by_difficulty(args.difficulty)
    else:
        dataset = get_dataset()

    if args.ids:
        id_set = {int(x.strip()) for x in args.ids.split(",")}
        dataset = [s for s in dataset if s.id in id_set]

    if args.samples:
        dataset = dataset[: args.samples]

    if not dataset:
        logger.error("No samples match the given filters")
        sys.exit(1)

    # ── Dry run ──────────────────────────────────────────
    if args.dry_run:
        summary = dataset_summary()
        print("\n  Dataset Summary")
        print(f"  Total: {summary['total_samples']} samples")
        print(f"  Categories: {summary['categories']}")
        print(f"  Difficulties: {summary['difficulties']}")
        print(f"  Frameworks: {summary['frameworks']}")
        print(f"\n  Selected for this run: {len(dataset)} samples")
        for s in dataset:
            print(f"    [{s.id:2d}] {s.difficulty:6s} | {s.category:24s} | {s.finding_text[:60]}...")
        return

    # ── Run evaluation ───────────────────────────────────
    logger.info("Starting evaluation: %d samples", len(dataset))
    overall_start = time.time()
    results: list[SampleResult] = []

    for i, sample in enumerate(dataset, 1):
        logger.info(
            "── Sample %d/%d (id=%d, %s) ──",
            i, len(dataset), sample.id, sample.category,
        )
        result = run_sample(sample)
        results.append(result)
        print(format_sample_report(result))

    overall_duration = time.time() - overall_start

    # ── Aggregate ────────────────────────────────────────
    agg = compute_aggregate_metrics(results)
    report = format_aggregate_report(agg)
    print(report)
    logger.info("Total evaluation time: %.1fs", overall_duration)

    # ── Write JSON output ────────────────────────────────
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = _serialize_results(results, agg)
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        logger.info("Results written to %s", output_path)


if __name__ == "__main__":
    main()
