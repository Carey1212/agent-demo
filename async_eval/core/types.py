"""核心数据结构。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EvalCase:
    """一条评测用例。"""
    case_id: str
    question: str
    agent_id: int = 1001
    expected_intent: str | None = None       # RECOMMEND/BUY/CONSULT/KNOWLEDGE/UNKNOWN
    expected_points: list[str] = field(default_factory=list)   # 期望覆盖的知识点/关键词
    expected_tools: list[str] = field(default_factory=list)    # 期望触发的工具线索（子串匹配 tool_result key）
    turns: list[str] = field(default_factory=list)             # 多轮追问
    tags: list[str] = field(default_factory=list)              # 场景标签


@dataclass
class Transcript:
    """一次完整执行采集到的内容。"""
    text: str = ""                              # 拼接后的完整输出
    tool_result: dict[str, Any] = field(default_factory=dict)
    tools_hit: list[str] = field(default_factory=list)         # 命中的工具 key
    errors: list[str] = field(default_factory=list)
    stopped: bool = False                       # 是否收到 1002 停止事件
    actual_intent: str | None = None            # 进程内模式从 graph state 提取
    first_token_ms: float | None = None
    total_ms: float | None = None
    timed_out: bool = False


@dataclass
class RuleMetrics:
    intent_ok: bool | None = None               # None 表示无法校验
    stream_ok: bool = False
    text_ok: bool = False                       # 输出非空
    tool_ok: bool | None = None                 # None 表示未声明期望工具
    has_error: bool = False


@dataclass
class EvalResult:
    case: EvalCase
    transcript: Transcript
    rule: RuleMetrics
    judge: dict[str, Any] | None = None          # LLM 判分结果
    judge_error: str | None = None

    def is_pass(self, threshold: float) -> bool:
        if self.judge and self.judge.get("overall") is not None:
            return float(self.judge["overall"]) >= threshold
        return self.rule.intent_ok is not False and not self.rule.has_error and self.rule.text_ok


def to_dict(obj: Any) -> Any:
    """dataclass -> dict，用于 JSON 报告。"""
    from dataclasses import fields, is_dataclass

    if is_dataclass(obj):
        return {f.name: to_dict(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, list):
        return [to_dict(x) for x in obj]
    if isinstance(obj, tuple):
        return [to_dict(x) for x in obj]
    if isinstance(obj, dict):
        return {k: to_dict(v) for k, v in obj.items()}
    return obj