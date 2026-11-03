"""控制台摘要输出。"""
from __future__ import annotations

from ..core.metrics import Summary


def render_summary(s: Summary) -> str:
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("评测摘要")
    lines.append("=" * 60)
    lines.append(f"用例总数             : {s.total}")
    if s.intent_accuracy is not None:
        lines.append(f"意图准确率           : {s.intent_accuracy}%  ({s.intent_total} 条可校验)")
    lines.append(f"流完成率(STOP)       : {s.stream_complete_rate}%")
    lines.append(f"有文本输出率         : {s.text_rate}%")
    if s.tool_accuracy is not None:
        lines.append(f"工具命中率           : {s.tool_accuracy}%  ({s.tool_total} 条声明)")
    lines.append(f"运行错误率           : {s.error_rate}%")
    if s.first_token_p50 is not None:
        lines.append(f"首字延迟 P50/P95     : {s.first_token_p50}ms / {s.first_token_p95}ms")
    if s.total_duration_p50 is not None:
        lines.append(f"总耗时 P50/P95       : {s.total_duration_p50}ms / {s.total_duration_p95}ms")
    if s.judge_overall_avg is not None:
        lines.append(f"Judge overall 均分   : {s.judge_overall_avg}")
    lines.append(f"通过率(≥阈值)        : {s.pass_rate}%  ({s.pass_count}/{s.total})")
    if s.by_intent:
        lines.append("-" * 60)
        lines.append("意图路由分布 (期望->实际)：")
        for k, v in sorted(s.by_intent.items(), key=lambda x: -x[1]["count"]):
            lines.append(f"  {k:28s} {v['count']} 例")
    lines.append("=" * 60)
    return "\n".join(lines)


def print_summary(s: Summary) -> None:
    print(render_summary(s))


def render_case_table(results) -> str:
    """逐用例精简表格。"""
    header = f"{'case_id':<26} {'intent(期望->实际)':<30} {'err':<4} {'TTFT':>8} {'overall':>8} {'pass':>5}"
    rows = [header, "-" * len(header)]
    for r in results:
        exp = r.case.expected_intent or "-"
        act = r.transcript.actual_intent or "-"
        err = "Y" if r.rule.has_error else "N"
        ttft = f"{r.transcript.first_token_ms:.0f}ms" if r.transcript.first_token_ms else "-"
        ov = r.judge.get("overall") if r.judge else "-"
        passed = "✓" if r.is_pass(0) and r.judge and r.judge.get("overall", 0) >= 3.5 else "✗"
        rows.append(
            f"{r.case.case_id:<26} {exp+'->'+act:<30} {err:<4} {ttft:>8} {str(ov):>8} {passed:>5}"
        )
    return "\n".join(rows)


def print_case_table(results) -> None:
    print(render_case_table(results))


def render_harvest_summary(res) -> str:
    lines = [
        "=" * 60,
        "生产会话回流采集",
        "=" * 60,
        f"会话总数(近N天)     : {res.total_sessions}",
        f"采样数              : {res.sampled_sessions}",
        f"成功恢复对话        : {res.recovered}",
        f"质量过滤(轮数不足)  : {res.filtered}",
        f"首问去重            : {res.deduped}",
        f"获取到意图真值      : {res.intents_found}",
        f"产出用例            : {len(res.cases)}",
    ]
    if res.output_path:
        lines.append(f"输出文件            : {res.output_path}")
    lines.append("=" * 60)
    return "\n".join(lines)


def print_harvest_summary(res) -> None:
    print(render_harvest_summary(res))