# ========================= 创建路由对象 =========================
from fastapi import APIRouter
from dao.ChatSessionDAO import chat_session_dao
from web.routers.ChatRouter import AGENTS  # 全局智能体实例字典

session_router = APIRouter()

# ========================= 创建新会话 =========================
@session_router.post("")
async def create_session(n: int, agent_id: int, user_id: int):
    """
    创建新的聊天会话，并返回会话VO
    - n: 随机选取的示例数量
    - agent_id: 智能体ID
    - user_id: 用户ID
    """
    return chat_session_dao.create_session(n, agent_id, user_id)

@session_router.get("/hot")
async def hot(n: int, agent_id: int):
    return chat_session_dao.hot_examples(n, agent_id)

# ========================= 查询指定会话详情 =========================
@session_router.get("/{agent_id}/{user_id}/{session_id}")
async def session_detail(agent_id: int, user_id: int, session_id: str):
    """
    查询某个具体会话的详情
    - agent_id: 智能体ID
    - user_id: 用户ID
    - session_id: 会话ID
    """
    agent = AGENTS.get(agent_id, None)
    if agent is None:
        return f"Agent not found (agentId={agent_id})"

    # 调用智能体的 session_detail 方法，获取会话详情
    return await agent.session_detail(user_id, session_id)


# ========================= 查询指定会话详情 =========================
@session_router.get("/history")
async def history(agent_id: int, user_id: int):
    """
    查询某个具体会话的详情
    - agent_id: 智能体ID
    - user_id: 用户ID
    - session_id: 会话ID
    """
    agent = AGENTS.get(agent_id, None)
    if agent is None:
        return f"Agent not found (agentId={agent_id})"

    # 调用智能体的 session_detail 方法，获取会话详情
    return chat_session_dao.query_history_session(agent_id, user_id)

# ========================= 删除历史会话 =========================
@session_router.delete("/history")
async def delete_history_session(agent_id: int, user_id: int, session_id: str):
    """
    删除指定历史会话
    - agent_id: 智能体ID
    - user_id: 用户ID
    - session_id: 会话ID
    """
    agent = AGENTS.get(agent_id, None)
    if agent is None:
        return f"Agent not found (agentId={agent_id})"

    # 调用智能体的删除方法
    await agent.delete_session(session_id)

    # 删除数据库中的历史会话
    return chat_session_dao.delete_history_session(agent_id, user_id, session_id)

# ========================= 更新历史会话标题 =========================
@session_router.put("/history")
async def update_history_session(agent_id: int, user_id: int, session_id: str, title: str):
    """
    更新历史会话标题
    - agent_id: 智能体ID
    - user_id: 用户ID
    - session_id: 会话ID
    - title: 新标题
    """
    return chat_session_dao.update_history_session(agent_id, user_id, session_id, title)
