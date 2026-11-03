"""HTTP 模式采集：直连已启动服务的 /chat SSE 接口（不 import agent，最解耦）。

前置：服务需已启动（且需先解决 RouterAgent.py 的 httpx2 误导入，才能起服务）。
HTTP 模式无法直接从 graph state 提取 intent，故 actual_intent 恒为 None。
"""
from __future__ import annotations

import time

import httpx

from .collector import EV_ERROR, EV_STOP, EV_TEXT, EV_TOOL, parse_sse_event, sanitize_id
from .types import EvalCase, Transcript


class HttpCollector:
    def __init__(self, run_id: str, base_url: str):
        self.run_id = run_id
        self.base_url = base_url.rstrip("/")

    def session_id_for(self, case: EvalCase) -> str:
        return f"eval_{self.run_id}_{sanitize_id(case.case_id)}"

    async def collect(
        self,
        case: EvalCase,
        session_id: str,
        user_token: str,
        timeout: float = 120.0,
    ) -> Transcript:
        tr = Transcript()
        start = time.perf_counter()
        first_token_ts: float | None = None

        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout=timeout)) as client:
            for query in [case.question, *case.turns]:
                try:
                    async with client.stream(
                        "POST",
                        f"{self.base_url}/chat",
                        json={
                            "question": query,
                            "sessionId": session_id,
                            "userToken": user_token,
                            "agentId": case.agent_id,
                        },
                    ) as resp:
                        async for line in resp.aiter_lines():
                            etype, data = parse_sseevent(line)
                            if etype == EV_TEXT:
                                if first_token_ts is None:
                                    first_token_ts = time.perf_counter()
                                tr.text += str(data or "")
                            elif etype == EV_TOOL and isinstance(data, dict):
                                tr.tool_result.update(data)
                            elif etype == EV_ERROR:
                                tr.errors.append(str(data))
                            elif etype == EV_STOP:
                                tr.stopped = True
                except Exception as e:  # noqa: BLE001
                    tr.errors.append(str(e))

        tr.total_ms = (time.perf_counter() - start) * 1000
        tr.first_token_ms = (first_token_ts - start) * 1000 if first_token_ts is not None else None
        return tr