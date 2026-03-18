"""Evaluation metrics for the GRC RAG pipeline.

Computes retrieval and mapping quality metrics by comparing pipeline output
against labeled ground-truth samples.

Metric categories:
  Retrieval   — context relevance, recall of expected controls in retrieved chunks
  Mapping     — precision, recall, F1 on predicted vs. expected control IDs
  Grounding   — faithfulness of citations to retrieved evidence
  End-to-end  — aggregated scores across the full dataset
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from src.evaluation.dataset import EvalSample
from src.retrieval.models import ControlMapping, QueryResponse

logger = logging.getLogger("evaluation.metrics")


# ── Per-sample result ────────────────────────────────────────────────────────


@dataclass
class SampleResult:
    """Evaluation result for a single sample."""

    sample_id: int
    finding_text: str
    difficulty: str
    category: str

    # Ground truth
    expected_controls: list[str] = field(default_factory=list)
    expected_domains: list[str] = field(default_factory=list)

    # Pipeline output
    predicted_controls: list[str] = field(default_factory=list)
    predicted_domains: list[str] = field(default_factory=list)
    mappings_returned: int = 0
    chunks_retrieved: int = 0
    chunks_after_rerank: int = 0
    approved_count: int = 0
    failed_count: int = 0

    # Timing & tokens
    duration_seconds: float = 0.0
    total_tokens: int = 0

    # Computed metrics
    control_precision: float = 0.0
    control_recall: float = 0.0
    control_f1: float = 0.0
    domain_recall: float = 0.0
    approval_rate: float = 0.0
    avg_confidence: float = 0.0

    # Errors
    error: str | None = None


def _normalize_control_id(cid: str) -> str:
    """Normalize control IDs for comparison (case-insensitive, strip whitespace)."""
    return cid.strip().upper().replace(" ", "")


def compute_sample_metrics(
    sample: EvalSample,
    response: QueryResponse | None,
    error: str | None = None,
) -> SampleResult:
    """Compute metrics for a single sample given a pipeline response."""
    result = SampleResult(
        sample_id=sample.id,
        finding_text=sample.finding_text,
        difficulty=sample.difficulty,
        category=sample.category,
        expected_controls=sample.expected_controls,
        expected_domains=sample.expected_domains,
    )

    if error or response is None:
        result.error = error or "No response"
        return result

    # Extract predicted control IDs and domains
    approved_mappings = [
        m for m in response.mappings if m.status.value == "APPROVED"
    ]
    all_mappings = response.mappings

    result.predicted_controls = [m.control_id for m in all_mappings]
    result.predicted_domains = list({m.domain for m in all_mappings})
    result.mappings_returned = len(all_mappings)
    result.chunks_retrieved = response.chunks_retrieved
    result.chunks_after_rerank = response.chunks_after_rerank
    result.approved_count = len(approved_mappings)
    result.failed_count = len(all_mappings) - len(approved_mappings)
    result.duration_seconds = response.duration_seconds
    result.total_tokens = response.token_usage.total_tokens

    # ── Control-level precision / recall / F1 ─────────────────
    expected_set = {_normalize_control_id(c) for c in sample.expected_controls}
    predicted_set = {_normalize_control_id(c) for c in result.predicted_controls}

    true_positives = expected_set & predicted_set

    if predicted_set:
        result.control_precision = len(true_positives) / len(predicted_set)
    if expected_set:
        result.control_recall = len(true_positives) / len(expected_set)
    if result.control_precision + result.control_recall > 0:
        result.control_f1 = (
            2 * result.control_precision * result.control_recall
            / (result.control_precision + result.control_recall)
        )

    # ── Domain recall ─────────────────────────────────────────
    expected_domains_set = {d.lower() for d in sample.expected_domains}
    predicted_domains_set = {d.lower() for d in result.predicted_domains}
    if expected_domains_set:
        domain_hits = expected_domains_set & predicted_domains_set
        result.domain_recall = len(domain_hits) / len(expected_domains_set)

    # ── Approval rate ─────────────────────────────────────────
    if all_mappings:
        result.approval_rate = len(approved_mappings) / len(all_mappings)

    # ── Average confidence ────────────────────────────────────
    if all_mappings:
        result.avg_confidence = sum(m.confidence_score for m in all_mappings) / len(all_mappings)

    return result


# ── Aggregate metrics ────────────────────────────────────────────────────────


@dataclass
class AggregateMetrics:
    """Aggregated evaluation metrics across the full dataset."""

    total_samples: int = 0
    successful_samples: int = 0
    failed_samples: int = 0

    # Control-level averages
    mean_control_precision: float = 0.0
    mean_control_recall: float = 0.0
    mean_control_f1: float = 0.0

    # Domain
    mean_domain_recall: float = 0.0

    # Quality
    mean_approval_rate: float = 0.0
    mean_avg_confidence: float = 0.0

    # Retrieval
    mean_chunks_retrieved: float = 0.0
    mean_chunks_after_rerank: float = 0.0

    # Cost
    mean_duration_seconds: float = 0.0
    total_tokens: int = 0
    mean_tokens_per_query: float = 0.0

    # By difficulty
    metrics_by_difficulty: dict[str, dict] = field(default_factory=dict)

    # By category
    metrics_by_category: dict[str, dict] = field(default_factory=dict)


def _group_mean(results: list[SampleResult], attr: str) -> float:
    """Compute mean of an attribute across non-error results."""
    valid = [getattr(r, attr) for r in results if r.error is None]
    return sum(valid) / len(valid) if valid else 0.0


def _compute_group_metrics(results: list[SampleResult]) -> dict:
    """Compute metric summary for a group of results."""
    valid = [r for r in results if r.error is None]
    n = len(valid)
    if n == 0:
        return {"count": len(results), "successful": 0}
    return {
        "count": len(results),
        "successful": n,
        "mean_control_precision": sum(r.control_precision for r in valid) / n,
        "mean_control_recall": sum(r.control_recall for r in valid) / n,
        "mean_control_f1": sum(r.control_f1 for r in valid) / n,
        "mean_domain_recall": sum(r.domain_recall for r in valid) / n,
        "mean_approval_rate": sum(r.approval_rate for r in valid) / n,
        "mean_avg_confidence": sum(r.avg_confidence for r in valid) / n,
    }


def compute_aggregate_metrics(results: list[SampleResult]) -> AggregateMetrics:
    """Compute aggregate metrics from per-sample results."""
    agg = AggregateMetrics()
    agg.total_samples = len(results)
    agg.successful_samples = sum(1 for r in results if r.error is None)
    agg.failed_samples = agg.total_samples - agg.successful_samples

    agg.mean_control_precision = _group_mean(results, "control_precision")
    agg.mean_control_recall = _group_mean(results, "control_recall")
    agg.mean_control_f1 = _group_mean(results, "control_f1")
    agg.mean_domain_recall = _group_mean(results, "domain_recall")
    agg.mean_approval_rate = _group_mean(results, "approval_rate")
    agg.mean_avg_confidence = _group_mean(results, "mean_avg_confidence")
    agg.mean_chunks_retrieved = _group_mean(results, "chunks_retrieved")
    agg.mean_chunks_after_rerank = _group_mean(results, "chunks_after_rerank")
    agg.mean_duration_seconds = _group_mean(results, "duration_seconds")

    valid = [r for r in results if r.error is None]
    agg.total_tokens = sum(r.total_tokens for r in valid)
    agg.mean_tokens_per_query = agg.total_tokens / len(valid) if valid else 0.0

    # By difficulty
    difficulties = {r.difficulty for r in results}
    for d in sorted(difficulties):
        group = [r for r in results if r.difficulty == d]
        agg.metrics_by_difficulty[d] = _compute_group_metrics(group)

    # By category
    categories = {r.category for r in results}
    for c in sorted(categories):
        group = [r for r in results if r.category == c]
        agg.metrics_by_category[c] = _compute_group_metrics(group)

    return agg
