"""SSE 采集（进程内模式 + 解析层）。"""
from __future__ import annotations

import asyncio
import json
import re
import time

from .types import EvalCase, Transcript

# SSE 事件类型
EV_TEXT = 1001
EV_STOP = 1002
EV_TOOL = 1003
EV_ERROR = 2001

_TOOL_KEY_HINTS = ("courseInfo", "PrePlaceOrder")  # 工具结果 key 前缀

_san_re = re.compile(r"[^a-zA-Z0-9_\-]")


def sanitize_id(text: str) -> str:
    return _san_re.sub("_", text) or "case"


def parse_sse_event(raw: str):
    """解析单条 SSE 字符串 -> (eventType, eventData)。"""
    text = raw.strip()
    if text.startswith("data:"):
        text = text[len("data:"):].strip()
    try:
        obj = json.loads(text)
    except Exception:
        return None, None
    return obj.get("eventType"), obj.get("eventData")


class Collector:
    """把 agent.execute 的 SSE 异步流收敛为 Transcript。

    支持多轮：同一 session_id 反复 execute，依赖 LangGraph checkpointer 记住上下文。
    注意：1002（TextAgent）无 checkpointer，多轮仅做文本累加，语义上视为独立回答。
    """

    def __init__(self, run_id: str):
        self.run_id = run_id

    def session_id_for(self, case: EvalCase) -> str:
        return f"eval_{self.run_id}_{sanitize_id(case.case_id)}"

    async def collect(
        self,
        agent,
        case: EvalCase,
        session_id: str,
        user_token: str,
        timeout: float = 120.0,
    ) -> Transcript:
        tr = Transcript()
        start = time.perf_counter()
        first_token_ts: float | None = None
        messages = [case.question, *case.turns]

        async def _run_one(query: str):
            nonlocal first_token_ts

            async def _stream():
                async for raw in agent.execute(query, session_id, user_token):
                    yield raw

            it = _stream()
            while True:
                try:
                    raw = await asyncio.wait_for(it.__anext__(), timeout=timeout)
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    tr.timed_out = True
                    tr.errors.append("timeout")
                    await it.aclose()
                    break

                etype, data = parse_sse_event(raw)
                if etype == EV_TEXT:
                    if first_token_ts is None:
                        first_token_ts = time.perf_counter()
                    tr.text += str(data or "")
                elif etype == EV_TOOL:
                    if isinstance(data, dict):
                        tr.tool_result.update(data)
                        for key in data.keys():
                            if any(key.startswith(h) for h in _TOOL_KEY_HINTS):
                                tr.tools_hit.append(key)
                elif etype == EV_ERROR:
                    tr.errors.append(str(data))
                elif etype == EV_STOP:
                    tr.stopped = True

        try:
            for query in messages:
                await _run_one(query)
        except Exception as e:  # noqa: BLE001
            tr.errors.append(str(e))

        tr.total_ms = (time.perf_counter() - start) * 1000
        tr.first_token_ms = (first_token_ts - start) * 1000 if first_token_ts is not None else None
        tr.actual_intent = await self._extract_intent(agent, session_id)

        await self._cleanup_session(agent, session_id)
        return tr

    async def _extract_intent(self, agent, session_id: str) -> str | None:
        """进程内模式：从 graph state 恢复意图字段。"""
        graph = getattr(agent, "graph", None)
        if graph is None:
            return None
        try:
            from langchain_core.runnables import RunnableConfig
            snap = await graph.aget_state(RunnableConfig(configurable={"thread_id": session_id}))
            return snap.values.get("intent")
        except Exception:
            return None

    async def _cleanup_session(self, agent, session_id: str) -> None:
        try:
            await agent.delete_session(session_id)
        except Exception:
            pass