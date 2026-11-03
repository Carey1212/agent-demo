"""反思改进：低分用例归因 + 生成候选 prompt（可配合 Nacos 热更新实现闭环）。"""
from __future__ import annotations

import asyncio
import json
import re
from collections import defaultdict
from typing import Any

from ..eval_config import EvalConfig
from .judge import build_openai_llm, normalize_judge
from .types import EvalResult

# 归因类别
CATEGORIES = [
    "intent_misroute",   # 意图误判/路由错误
    "hallucination",     # 编造事实（课程/价格/工具数据）
    "tool_error",        # 工具调用/参数错误
    "incompleteness",    # 回答不完整
    "expression",        # 表达/格式问题
    "other",
]

# 受影响 prompt 名 -> system_prompt_config 属性名
PROMPT_ATTRS = {
    "route": "chat_route_message",
    "recommend": "chat_recommend_message",
    "buy": "chat_buy_message",
    "consult": "chat_consult_message",
    "knowledge": "chat_knowledge_message",
    "unknown": "chat_unknown_message",
    "text": "chat_text_message",
    "a2a": "chat_a2a_message",
}

_ATTR_TO_NAME = {v: k for k, v in PROMPT_ATTRS.items()}

_ATTRIBUTE_SYS = """你是 AI 问答系统的缺陷归因分析师。请根据给定用例的期望、实际输出与判分理由，
输出一个 JSON 对象，定位这条用例失败的根本原因，字段如下：

{"category": "...", "root_cause": "...", "affected_prompt": "...", "suggestion": "..."}

- category 必须是下列之一：intent_misroute / hallucination / tool_error / incompleteness / expression / other
- affected_prompt 必须是下列之一：route / recommend / buy / consult / knowledge / unknown / text / a2a
  （意图路由问题选 route；涉及课程/价格事实编造选 knowledge/consult；回答表达问题选对应业务节点或 text）
- suggestion 是一句针对该系统提示词的具体改进建议（可执行，不是空话）
只输出 JSON，不要输出其他文字。"""


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    else:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            text = m.group(0)
    try:
        return json.loads(text)
    except Exception:
        return {}


def _build_case_text(r: EvalResult) -> str:
    j = normalize_judge(r)
    parts = [
        f"用例ID：{r.case.case_id}",
        f"问题：{r.case.question}",
    ]
    if r.case.expected_intent:
        parts.append(f"期望意图：{r.case.expected_intent}")
    if r.transcript.actual_intent:
        parts.append(f"实际意图：{r.transcript.actual_intent}")
    if r.case.expected_points:
        parts.append(f"期望要点：{'、'.join(r.case.expected_points)}")
    if r.case.expected_tools:
        parts.append(f"期望工具：{'、'.join(r.case.expected_tools)}")
    parts.append(f"助手输出：\n{r.transcript.text.strip() or '(空)'}")
    if r.transcript.errors:
        parts.append(f"运行错误：{' | '.join(r.transcript.errors)}")
    parts.append(f"判分：overall={j.get('overall')} reason={j.get('reason')} issues={j.get('issues')}")
    return "\n".join(parts)


async def attribute(llm, r: EvalResult) -> dict[str, Any]:
    from langchain_core.messages import SystemMessage, HumanMessage

    resp = await llm.ainvoke([SystemMessage(_ATTRIBUTE_SYS), HumanMessage(_build_case_text(r))])
    content = resp.content if not isinstance(resp, str) else resp
    if isinstance(content, list):
        content = "".join(str(c) for c in content)
    out = _extract_json(str(content))
    if out.get("category") not in CATEGORIES:
        out["category"] = "other"
    if out.get("affected_prompt") not in PROMPT_ATTRS:
        # 根据 agent 做个兜底映射
        out["affected_prompt"] = {1001: "route", 1002: "text", 1003: "a2a"}[r.case.agent_id]
    out["case_id"] = r.case.case_id
    return out


def aggregate(attributions: list[dict[str, Any]]) -> dict[str, Any]:
    """按归因类别 + 受影响 prompt 聚合，形成可执行的缺陷画像。"""
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for a in attributions:
        by_cat[a["category"]].append(a)

    summary: dict[str, Any] = {}
    for cat in CATEGORIES:
        items = by_cat.get(cat, [])
        summary[cat] = {
            "count": len(items),
            "cases": [i["case_id"] for i in items],
            "suggestions": list({i["suggestion"] for i in items if i.get("suggestion")}),
            "prompts": sorted({i["affected_prompt"] for i in items}),
        }
    return summary


_GEN_CANDIDATE_SYS = """你是一名资深 prompt 工程师。下面给出某一类智能体的当前系统提示词，以及评测发现的改进建议。
请输出优化后的完整系统提示词（保持原有职责与结构，仅针对建议做精准增强，不要多余解释）。
直接输出改进后的提示词正文。"""


async def generate_candidate(llm, current_prompt: str, suggestions: list[str]) -> str:
    from langchain_core.messages import SystemMessage, HumanMessage

    human = f"【当前系统提示词】\n{current_prompt}\n\n【改进建议】\n" + "\n".join(
        f"- {s}" for s in suggestions
    )
    resp = await llm.ainvoke([SystemMessage(_GEN_CANDIDATE_SYS), HumanMessage(human)])
    content = resp.content if not isinstance(resp, str) else resp
    if isinstance(content, list):
        content = "".join(str(c) for c in content)
    return str(content).strip()


def get_current_prompt(prompt_name: str) -> str:
    """从 system_prompt_config 读取当前 prompt 文本（进程内）。"""
    from agent.prompts import system_prompt_config

    attr = PROMPT_ATTRS.get(prompt_name)
    if attr is None:
        raise ValueError(f"未知 prompt 名：{prompt_name}（可选：{list(PROMPT_ATTRS)}）")
    return getattr(system_prompt_config, attr, "") or ""


async def reflect(
    cfg: EvalConfig,
    low_score_results: list[EvalResult],
    generate_candidates: bool = True,
) -> dict[str, Any]:
    """对低分用例做归因，并按受影响 prompt 生成候选改进版。"""
    llm = build_openai_llm(cfg, temperature=0.2)
    sem = asyncio.Semaphore(cfg.judge_concurrency)

    async def one(r: EvalResult):
        async with sem:
            return await attribute(llm, r)

    attributions = await asyncio.gather(*(one(r) for r in low_score_results))
    summary = aggregate(list(attributions))

    candidates: dict[str, str] = {}
    if generate_candidates:
        for prompt_name, attr in PROMPT_ATTRS.items():
            suggs = []
            for a in attributions:
                if a["affected_prompt"] == prompt_name and a.get("suggestion"):
                    suggs.append(a["suggestion"])
            suggs = list(dict.fromkeys(suggs))
            if not suggs:
                continue
            try:
                current = get_current_prompt(prompt_name)
                if current:
                    candidates[prompt_name] = await generate_candidate(llm, current, suggs)
            except Exception as e:  # noqa: BLE001
                candidates[prompt_name] = f"ERROR: {e}"

    return {
        "attributions": attributions,
        "summary": summary,
        "candidates": candidates,
    }