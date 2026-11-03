"""规则指标聚合 + 汇总统计。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .types import EvalResult


def _pct(num: int, den: int) -> float:
    return round(100 * num / den, 2) if den else 0.0


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    vals = sorted(values)
    idx = min(len(vals) - 1, int(len(vals) * p))
    return round(vals[idx], 1)


@dataclass
class Summary:
    total: int = 0
    intent_accuracy: float | None = None          # 无期望意图时为 None
    intent_total: int = 0
    stream_complete_rate: float = 0.0
    text_rate: float = 0.0
    tool_accuracy: float | None = None
    tool_total: int = 0
    error_rate: float = 0.0
    first_token_p50: float | None = None
    first_token_p95: float | None = None
    total_duration_p50: float | None = None
    total_duration_p95: float | None = None
    judge_overall_avg: float | None = None
    pass_count: int = 0
    pass_rate: float = 0.0
    by_intent: dict[str, dict[str, Any]] = field(default_factory=dict)


def summarize(results: list[EvalResult], pass_threshold: float) -> Summary:
    s = Summary(total=len(results))

    intent_ok = 0
    intent_total = 0
    stream_ok = 0
    text_ok = 0
    tool_ok = 0
    tool_total = 0
    err = 0
    ft = []
    td = []
    judge_scores = []
    pass_cnt = 0

    intent_bucket: dict[str, list] = {}
    for r in results:
        if r.rule.intent_ok is not None:
            intent_total += 1
            if r.rule.intent_ok:
                intent_ok += 1
        if r.rule.stream_ok:
            stream_ok += 1
        if r.rule.text_ok:
            text_ok += 1
        if r.rule.tool_ok is not None:
            tool_total += 1
            if r.rule.tool_ok:
                tool_ok += 1
        if r.rule.has_error:
            err += 1
        if r.transcript.first_token_ms is not None:
            ft.append(r.transcript.first_token_ms)
        if r.transcript.total_ms is not None:
            td.append(r.transcript.total_ms)

        if r.judge and r.judge.get("overall") is not None:
            judge_scores.append(float(r.judge["overall"]))
        if r.is_pass(pass_threshold):
            pass_cnt += 1

        if r.case.expected_intent:
            bucket = intent_bucket.setdefault(
                f"{r.case.expected_intent}->{r.transcript.actual_intent}", []
            )
            bucket.append(r.case.case_id)

    s.intent_total = intent_total
    s.intent_accuracy = _pct(intent_ok, intent_total) if intent_total else None
    s.stream_complete_rate = _pct(stream_ok, len(results))
    s.text_rate = _pct(text_ok, len(results))
    s.tool_total = tool_total
    s.tool_accuracy = _pct(tool_ok, tool_total) if tool_total else None
    s.error_rate = _pct(err, len(results))
    s.first_token_p50 = _percentile(ft, 0.5)
    s.first_token_p95 = _percentile(ft, 0.95)
    s.total_duration_p50 = _percentile(td, 0.5)
    s.total_duration_p95 = _percentile(td, 0.95)
    s.judge_overall_avg = round(sum(judge_scores) / len(judge_scores), 2) if judge_scores else None
    s.pass_count = pass_cnt
    s.pass_rate = _pct(pass_cnt, len(results))
    s.by_intent = {k: {"cases": v, "count": len(v)} for k, v in intent_bucket.items()}

    return s