"""真实会话回流：从 MySQL chat_session（会话清单）+ PG checkpointer（对话全文）采集生产用例。

思路：
    chat_session ──采样──> session_id 列表
        │
        ▼
    graph.aget_state / aget_state_history ──> 每轮 messages + intent(真值)
        │
        ▼
    切分 → 标签化(expected_intent 来自 checkpoint 真值, expected_tools 来自 ToolMessage)
        │
        ▼
    质量过滤 + 去重 → datasets/harvested_<date>.yaml

说明：
- 仅支持有 graph + checkpointer 的 agent（1001 / 1003）；1002 TextAgent 无记忆不支持采集。
- 只读取数据，不产生 LLM 调用（除非开启 --label 生成 expected_points）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select

from ..core.types import EvalCase
from ..eval_config import ASYNC_EVAL_DIR

# 工具名 → collector/runner 的匹配 hint（与 core.collector._TOOL_KEY_HINTS 保持一致）
TOOL_HINTS: dict[str, str] = {
    "query_course_by_id": "courseInfo",
    "pre_place_order": "PrePlaceOrder",
}

_SUPPORTED_AGENTS = (1001, 1003)


@dataclass
class HarvestResult:
    total_sessions: int = 0
    sampled_sessions: int = 0
    recovered: int = 0
    filtered: int = 0
    deduped: int = 0
    intents_found: int = 0
    cases: list[EvalCase] = field(default_factory=list)
    output_path: Path | None = None


def _norm_question(q: str) -> str:
    return re.sub(r"\s+", "", q or "").strip().lower()


def _sample_sessions(agent_id: int, days: int, limit: int) -> list[dict[str, Any]]:
    """从 MySQL chat_session 采样会话清单（仅元数据）。"""
    from config import SessionLocal
    from dao.pojo import ChatSession

    since = datetime.now() - timedelta(days=days)
    with SessionLocal() as session:
        rows = session.execute(
            select(
                ChatSession.session_id,
                ChatSession.title,
                ChatSession.update_time,
            )
            .where(ChatSession.agent_id == agent_id)
            .where(ChatSession.title.isnot(None))
            .where(ChatSession.update_time >= since)
            .order_by(ChatSession.update_time.desc())
            .limit(limit)
        ).all()
    return [
        {
            "session_id": str(r[0]),
            "title": str(r[1] or ""),
            "update_time": r[2],
        }
        for r in rows
    ]


async def _recover_transcript(agent, session_id: str) -> dict[str, Any] | None:
    """从 checkpointer 恢复某会话的 messages + 每轮意图真值。"""
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from langchain_core.runnables import RunnableConfig

    graph = getattr(agent, "graph", None)
    if graph is None:
        return None

    cfg = RunnableConfig(configurable={"thread_id": session_id})
    snap = await graph.aget_state(cfg)
    messages = snap.values.get("messages", [])
    if not messages:
        # 空会话不采集
        return None

    # 每轮：第 i 条 HumanMessage 出现后的消息总数 -> 该轮意图
    intent_by_count: dict[int, str] = {}
    try:
        async for s in graph.aget_state_history(cfg, limit=10000):
            n = len(s.values.get("messages", []))
            intent = s.values.get("intent")
            if intent and n not in intent_by_count:
                intent_by_count[n] = str(intent)
    except Exception:  # noqa: BLE001  历史不可用时兜底最新 intent
        intent = snap.values.get("intent")
        if intent:
            intent_by_count[len(messages)] = str(intent)

    humans: list[str] = []
    counts: list[int] = []
    cur = 0
    for m in messages:
        cur += 1
        if isinstance(m, HumanMessage) and m.content:
            humans.append(str(m.content))
            counts.append(cur)

    assts = [str(m.content) for m in messages if isinstance(m, AIMessage) and m.content]
    tool_names = [str(m.name) for m in messages if isinstance(m, ToolMessage) and m.name]

    if not humans or not any(a.strip() for a in assts):
        return None

    return {
        "humans": humans,
        "counts": counts,
        "assistant_output": "\n".join(a for a in assts if a.strip()),
        "tool_names": list(dict.fromkeys(tool_names)),
        "intent_by_count": intent_by_count,
        "total_count": cur,
    }


def _label_points(llm, case: EvalCase, assistant_output: str, tool_names: list[str]) -> list[str]:
    """用 deepseek 从真实输出标注 expected_points（仅 --label 时调用）。"""
    from langchain_core.messages import HumanMessage, SystemMessage

    sys_prompt = (
        "你是评测数据标注助手。给定一条真实用户问题、助手回答和命中的工具，"
        "请输出 JSON：{\"expected_points\":[\"...\",\"...\"]}，"
        "列出这条用例应当覆盖的关键知识点（2-5 个，来自助手回答的内容主题，不含工具名）。"
        "只输出 JSON，不要其他文字。"
    )
    human = (
        f"【问题】{case.question}\n"
        + (f"【追问】{' | '.join(case.turns)}\n" if case.turns else "")
        + f"【助手回答】\n{assistant_output[:2000]}\n"
        + (f"【命中的工具】{','.join(tool_names)}\n" if tool_names else "")
    )
    import json

    try:
        resp = llm.ainvoke([SystemMessage(sys_prompt), HumanMessage(human)])
        content = resp.content if not isinstance(resp, str) else resp
        if isinstance(content, list):
            content = "".join(str(c) for c in content)
        text = str(content).strip()
        m = re.search(r"\{.*\}", text, re.DOTALL)
        data = json.loads(m.group(0)) if m else {}
        points = data.get("expected_points", [])
        return [str(p) for p in points if str(p).strip()][:5]
    except Exception:  # noqa: BLE001
        return []


async def _build_cases(
    agent,
    sessions: list[dict[str, Any]],
    *,
    agent_id: int,
    min_turns: int,
    max_turns: int,
    max_cases: int,
    label_points: bool,
) -> HarvestResult:
    """恢复对话并切分为 EvalCase。"""
    res = HarvestResult()
    res.sampled_sessions = len(sessions)
    seen_questions: set[str] = set()
    llm = None
    if label_points:
        from .judge import build_openai_llm
        from ..eval_config import EvalConfig

        llm = build_openai_llm(EvalConfig.defaults(), temperature=0.0)

    for row in sessions:
        session_id = row["session_id"]
        rec = await _recover_transcript(agent, session_id)
        if rec is None:
            continue
        res.recovered += 1

        humans = rec["humans"]
        counts = rec["counts"]

        # 滑动窗口：取最近 max_turns+1 轮，保证末轮意图真值可用
        window = max_turns + 1
        start = max(0, len(humans) - window)
        q_humans = humans[start:]
        q_counts = counts[start:]

        if len(q_humans) < min_turns:
            res.filtered += 1
            continue

        question = q_humans[0]
        key = _norm_question(question)
        if key in seen_questions:
            res.deduped += 1
            continue
        seen_questions.add(key)

        final_intent = rec["intent_by_count"].get(q_counts[-1])
        if final_intent:
            res.intents_found += 1

        tool_hints = list(dict.fromkeys(TOOL_HINTS.get(t) for t in rec["tool_names"] if t in TOOL_HINTS))

        case = EvalCase(
            case_id=f"prod_{session_id[:10]}_{start}",
            question=question,
            agent_id=agent_id,
            expected_intent=final_intent,
            expected_tools=tool_hints,
            turns=q_humans[1:],
            tags=[
                "prod",
                f"agent:{agent_id}",
                "intents:" + ",".join(rec["intent_by_count"].get(c, "-") for c in q_counts),
            ],
        )

        if label_points and llm is not None:
            case.expected_points = await _label_points(llm, case, rec["assistant_output"], rec["tool_names"])

        res.cases.append(case)
        if max_cases and len(res.cases) >= max_cases:
            break

    return res


def _write_yaml(cases: list[EvalCase], agent_id: int, days: int) -> Path:
    """写为 datasets/harvested_<date>.yaml。"""
    date_str = datetime.now().strftime("%Y%m%d")
    path = ASYNC_EVAL_DIR / "datasets" / f"harvested_{date_str}.yaml"

    rows = []
    for c in cases:
        item: dict[str, Any] = {
            "case_id": c.case_id,
            "question": c.question,
            "agent_id": c.agent_id,
            "tags": c.tags,
        }
        if c.turns:
            item["turns"] = c.turns
        if c.expected_intent:
            item["expected_intent"] = c.expected_intent
        if c.expected_tools:
            item["expected_tools"] = c.expected_tools
        if c.expected_points:
            item["expected_points"] = c.expected_points
        rows.append(item)

    import yaml

    header = (
        f"# 生产会话回流采集 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"# 来源: chat_session(agent_id={agent_id}, 近{days}天) + PG checkpointer\n"
        f"# 脱敏: 仅保留 session_id 前缀, 不含 user_id 等个人信息\n"
    )
    content = header + yaml.safe_dump(
        {"cases": rows}, allow_unicode=True, sort_keys=False, default_flow_style=False
    )
    path.write_text(content, encoding="utf-8")
    return path


async def harvest(
    *,
    agent_id: int,
    days: int,
    limit: int,
    min_turns: int,
    max_turns: int,
    max_cases: int,
    label_points: bool,
    dry_run: bool,
) -> HarvestResult:
    """执行一次生产会话回流采集。"""
    if agent_id not in _SUPPORTED_AGENTS:
        raise ValueError(f"仅支持 agent_id {_SUPPORTED_AGENTS}（1002 TextAgent 无记忆，不支持采集）")

    from . import context

    await context.prepare([agent_id])
    try:
        agent = await context.get_agent(agent_id)
        sessions = _sample_sessions(agent_id, days, limit)
        res = await _build_cases(
            agent,
            sessions,
            agent_id=agent_id,
            min_turns=min_turns,
            max_turns=max_turns,
            max_cases=max_cases,
            label_points=label_points,
        )
    finally:
        await context.cleanup([agent_id])

    res.total_sessions = len(sessions)

    if not dry_run and res.cases:
        path = _write_yaml(res.cases, agent_id, days)
        res.output_path = path
    return res