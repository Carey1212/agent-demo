"""透传并采集 SSE 数据，评测仅在独立的后台任务处理器中执行。"""
import asyncio
import json
import logging
import os
from time import monotonic
from uuid import uuid4
from .schemas import Snapshot
from .prompt_context import prompt_captures

logger = logging.getLogger(__name__)


class Collector:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.buffer = ""

    def feed(self, chunk):
        # 一条 SSE 消息可能跨越多个数据块，一个数据块也可能包含多条消息。
        self.buffer += chunk
        self.buffer = self.buffer.replace("\r\n", "\n")
        while "\n\n" in self.buffer:
            frame, self.buffer = self.buffer.split("\n\n", 1)
            data = "\n".join(line[5:].lstrip() for line in frame.splitlines() if line.startswith("data:"))
            if not data:
                continue
            try:
                event = json.loads(data)
                kind, value = event.get("eventType"), event.get("eventData")
                if kind == 1001 and isinstance(value, str):
                    self.snapshot.answer += value
                elif kind == 1003 and isinstance(value, dict):
                    self.snapshot.tool_results.append(value)
                elif kind == 2001:
                    self.snapshot.errors.append(str(value))
                    self.snapshot.status = "execution_error"
            except (ValueError, AttributeError):
                self.snapshot.errors.append("SSE 事件格式无效")
                self.snapshot.status = "execution_error"


async def submit(snapshot):
    from config import get_async_pg_pool
    from .repository import Repository
    await Repository(await get_async_pg_pool()).submit(snapshot)


async def observe(source, question, session_id, agent_id, is_stopped=None, submitter=submit):
    snapshot = Snapshot(run_id=str(uuid4()), question=question, session_id=session_id, agent_id=agent_id)
    collector = Collector(snapshot)
    started = monotonic()
    try:
        iterator = aiter(source)
        while True:
            # 仅在 Agent 运行期间激活采集，yield 给前端时不泄漏请求上下文。
            context_token = prompt_captures.set(snapshot.prompts)
            try:
                chunk = await anext(iterator)
            except StopAsyncIteration:
                break
            finally:
                prompt_captures.reset(context_token)
            collector.feed(chunk)
            yield chunk
        if is_stopped and await asyncio.to_thread(is_stopped, session_id):
            snapshot.status = "interrupted"
    except (asyncio.CancelledError, GeneratorExit):
        snapshot.status = "interrupted"
        raise
    except Exception as exc:
        snapshot.status = "execution_error"
        snapshot.errors.append(f"Agent 执行异常：{type(exc).__name__}")
        raise
    finally:
        if snapshot.prompts:
            snapshot.missing_evidence = [
                item for item in snapshot.missing_evidence if item != "prompt_version"
            ]
        snapshot.duration_ms = (monotonic() - started) * 1000
        # 请求处理中仅提交任务，不调用评测模型；提交失败不能影响回答输出。
        try:
            async with asyncio.timeout(3):
                await submitter(snapshot)
            logger.info("评测任务已入队，执行 ID：%s", snapshot.run_id)
        except (Exception, asyncio.CancelledError) as exc:
            logger.warning("评测任务提交失败，执行 ID：%s，异常类型：%s", snapshot.run_id, type(exc).__name__)


def observe_if_enabled(source, question, session_id, agent_id, is_stopped=None):
    # 通过显式开关启用采集，避免在确认部署前将会话数据发送给评测模型。
    if os.getenv("AGENT_EVAL_ENABLED", "false").lower() != "true":
        return source
    return observe(source, question, session_id, agent_id, is_stopped)
