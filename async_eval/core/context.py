"""运行上下文：惰性加载 agent、绕过已知 import 问题、准备/清理资源。

说明：
- RouterAgent.py 顶部存在 `from httpx2 import _content`（`httpx2` 为误导入），
  会导致 `import agent.tianji` 直接失败。这里通过注册一个假的 `httpx2` 模块来绕过，
  不改动任何现有文件。
- 由于 `agent/__init__.py` 会级联导入 `tja2a_client`（模块级 `asyncio.run` 连接 A2A 服务），
  因此无论评测哪个 agent，都要求 A2A 服务与 Postgres/Nacos/Redis 可达。
"""
from __future__ import annotations

import sys
import types

_AGENTS: dict[int, object] = {}
_PREPARED = False


def _ensure_httpx2_shim() -> None:
    """给不存在的 httpx2 包打补丁，绕过 RouterAgent.py 的误导入。"""
    if "httpx2" not in sys.modules:
        mod = types.ModuleType("httpx2")
        mod._content = None  # 该变量并未被实际使用
        sys.modules["httpx2"] = mod


def _resolve_agent(agent_id: int):
    """按 id 惰性导入对应 agent 单例，避免无关导入。"""
    _ensure_httpx2_shim()
    if agent_id == 1001:
        from agent.tianji import router_agent
        return router_agent
    if agent_id == 1002:
        from agent.tianji import text_agent
        return text_agent
    if agent_id == 1003:
        from agent.tja2a import tja2a_agent
        return tja2a_agent
    raise ValueError(f"未知 agent_id={agent_id}（支持 1001/1002/1003）")


async def get_agent(agent_id: int):
    """获取并（首次）初始化 agent 实例，结果缓存。"""
    if agent_id not in _AGENTS:
        agent = _resolve_agent(agent_id)
        await agent.init()
        _AGENTS[agent_id] = agent
    return _AGENTS[agent_id]


async def prepare(agent_ids: list[int]) -> None:
    """进程内模式准备：初始化 PG 连接池 + 所有相关 agent。"""
    global _PREPARED
    _ensure_httpx2_shim()
    from config import get_async_pg_pool

    await get_async_pg_pool()
    for aid in sorted(set(agent_ids)):
        await get_agent(aid)
    _PREPARED = True


async def cleanup(agent_ids: list[int]) -> None:
    """进程内模式清理：销毁 agent、关闭 PG 连接池。"""
    global _PREPARED
    for aid in sorted(set(agent_ids)):
        agent = _AGENTS.get(aid)
        if agent is not None:
            try:
                await agent.destroy()
            except Exception:
                pass
    _AGENTS.clear()

    try:
        from config import close_async_pg_pool
        await close_async_pg_pool()
    except Exception:
        pass
    _PREPARED = False