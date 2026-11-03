from async_eval_codex.collection.prompt_context import record_prompt
import asyncio
import uuid
from typing import Optional, Dict

from a2a.types import TaskArtifactUpdateEvent, TaskStatusUpdateEvent
from httpx import AsyncClient
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.config import get_stream_writer
from langgraph.constants import START, END
from langgraph.graph import MessagesState
from langgraph.graph.state import CompiledStateGraph, StateGraph
from langgraph.types import StateSnapshot

from agent.BaseAgent import *
from config import get_async_pg_pool, config_manager, logger
from agent.prompts import system_prompt_config
from agent.tja2a import tja2a_client, timeout_config


class A2AState(MessagesState):
    """
    自定义对话状态结构，继承自 LangGraph 的 MessagesState。
    增加意图字段，用于在各节点间传递解析后的意图。
    """
    intent: str

class TJA2AAgent(BaseAgent):
    """ 基于A2A服务，实现天机AI助手业务 """

    def __init__(self, provider: str = "openai"):
        self.provider = provider
        self.llm: Optional[BaseChatModel] = None
        self.checkpointer: Optional[AsyncPostgresSaver] = None
        self.graph: Optional[CompiledStateGraph] = None

    async def init(self):
        """
        初始化 Agent，包括：
        - 检查checkpointer表是否存在
        - LLM 初始化
        - 状态图构建
        """
        # 检查会话记忆所需要的表是否存在
        await self.check_table()

        # 获取异步数据库连接池，并且创建一个 PostgresSaver 用于对话记忆
        async_pg_pool = await get_async_pg_pool()
        self.checkpointer = AsyncPostgresSaver(conn=async_pg_pool)

        self.init_llm()
        self.init_graph()

    async def check_table(self):
        """
        检查数据库中是否存在会话记忆相关的表，如果不存在则创建
        """
        async_pg_pool = await get_async_pg_pool()
        conn = None
        try:
            # 先创建一个临时checkpointer，用于检查数据库中相关的表是否存在，如果不存在则创建
            conn = await async_pg_pool.getconn()
            await conn.set_autocommit(True)  # 设置自动提交，不使用显式事务块
            checkpointer = AsyncPostgresSaver(conn=conn)
            await checkpointer.setup()  # 如果会话记忆相关的表不存在，会自动创建
        finally:
            if conn is not None:
                await async_pg_pool.putconn(conn)

    def init_llm(self):
        """
        初始化 LLM（chat model）
        """
        cfg = config_manager
        prefix = f"ai.{self.provider}"

        # 读取 LLM 配置
        model_name = cfg.get(f"{prefix}.model")
        api_key = cfg.get(f"{prefix}.api-key")
        base_url = cfg.get(f"{prefix}.base-url")
        temperature = float(cfg.get(f"{prefix}.temperature", 0.7))
        timeout = int(cfg.get(f"{prefix}.timeout", 60))

        # 使用 LangChain 初始化模型
        self.llm = init_chat_model(
            model=model_name,
            model_provider="openai",
            api_key=api_key,
            base_url=base_url,
            temperature=temperature,
            timeout=timeout
        )

    def init_graph(self):
        """
        构建 LangGraph 图模型：

            START → intent_agent → work_agent → END

        - intent_agent：基于 LLM 的意图识别
        - work_agent：调用 TJA2A 服务执行任务（流式输出）
        """
        builder = StateGraph(A2AState)

        # 添加节点
        builder.add_node('intent_agent',self.intent_agent)
        builder.add_node('work_agent',self.work_agent)

        # 注册边
        builder.add_edge(START, "intent_agent")
        builder.add_edge("intent_agent", "work_agent")
        builder.add_edge("work_agent", END)

        # 编译图
        self.graph = builder.compile(self.checkpointer)

    # ----------------------------------------------------------------------------------------
    # 节点：意图识别 Agent
    # ----------------------------------------------------------------------------------------
    async def intent_agent(self, state: A2AState) -> Dict[str, Any]:
        """
        通过 LLM 对用户意图进行分类。

        输入：
            state["messages"] 中的最后一条用户消息
        输出：
            {"intent": "..."} 意图名称
        """
        last_user_msg = state["messages"][-1]

        # 获取agent卡片
        # 卡片内容太多，只去重要的
        cards = [{
            'name': name,
            'description': card.description,
            'skills': card.skills,
        } for name, card in tja2a_client.agent_cards.items()]
            
        # 读取提示词配置内容
        template = system_prompt_config.chat_a2a_message
        record_prompt("chat_a2a_message", template)
        system_prompt = template.format(agent_cards=cards)

        # LLM 推断意图
        res = await self.llm.ainvoke([
            SystemMessage(content=system_prompt),
            last_user_msg,
        ])

        # 传递意图结果，是后续执行智能体的名称
        return {"intent": res.content}

    # ----------------------------------------------------------------------------------------
    # 节点：执行 TJA2A 工具 Agent
    # ----------------------------------------------------------------------------------------
    async def work_agent(self, state: A2AState, config: RunnableConfig) -> Dict[str, Any]:
        """
        执行 TJA2A 服务（流式响应）。

        - 根据识别的意图调用具体 TJA2A agent
        - 从 TJA2A 接受流式事件（Artifact + Status）
        - 将内容写入 SSE 输出流（writer）
        """

        writer = get_stream_writer()

        # 获取前一步识别的 agent 名称
        agent_name = state.get("intent", "UnknownAgent")
        logger.debug(f"意图识别到的agent_name: {agent_name}")

        # 从外部传入 runnable config，提取参数
        cfg = config.get("configurable", {})
        user_token = cfg.get("user_token", "")
        request_id = cfg.get("request_id", "")
        question = cfg.get("question", "")

        # 构建历史消息，用于 TJA2A 记忆
        messages = state["messages"]
        history = []
        for msg in messages[:-1]:
            if isinstance(msg, HumanMessage):
                history.append({"type": "USER", "content": msg.content})
            elif isinstance(msg, AIMessage):
                history.append({"type": "ASSISTANT", "content": msg.content})

        current_messages = []  # 存储当前会话消息
        response_metadata = {}  # 存储工具响应元数据

        # 与 TJA2A 服务交互
        async with AsyncClient(timeout=timeout_config) as httpx_client:
            async for event in tja2a_client.send_message(
                    question=question,
                    request_id=request_id,
                    user_token=user_token,
                    agent_name=agent_name,
                    httpx_client=httpx_client,
                    history=history
            ):
                update_event = event[1]

                # 文本内容流事件
                if isinstance(update_event, TaskArtifactUpdateEvent):
                    text = update_event.artifact.parts[0].root.text
                    if text:
                        current_messages.append(text)
                        writer({"text": text})  # 推给 SSE

                # 结束事件（返回工具执行结果）
                elif isinstance(update_event, TaskStatusUpdateEvent) and update_event.final and update_event.metadata:
                    writer({"tool_result": update_event.metadata.get("tool_result")})
                    response_metadata = update_event.metadata

        # 写入最终 AIMessage （因为langchain没有调用大模型，不会自动加到messages中，所以需要手动添加）
        messages.append(AIMessage(
            content="".join(current_messages),
            response_metadata=response_metadata
        ))

        return {"status": "completed", "messages": messages}


    # ----------------------------------------------------------------------------------------
    # Agent 执行入口（外部接口）
    # ----------------------------------------------------------------------------------------
    async def execute(self, question: str, session_id: str, user_token: str) -> AsyncIterable[str]:
        """
        执行一次完整的 Agent 对话流程，返回 SSE 流式数据。

        流数据类型：
        - 1001：普通文本（LLM / TJA2A 输出）
        - 1003：工具执行结果
        - 2001：错误信息
        - STOP_EVENT：流结束通知
        """

        self.reset_stop(session_id)
        request_id = uuid.uuid4().hex

        try:
            # 更新会话标题
            from dao import chat_session_dao
            chat_session_dao.update_title(session_id, self.id(), question)

            # 构建 runnable config
            config = RunnableConfig(configurable={
                "thread_id": session_id,
                "user_token": user_token,
                "request_id": request_id,
                "question": question,
            })

            inputs = {"messages": HumanMessage(
                content=question,
                metadata={"request_id": request_id}
            )}

            # LangGraph 流式执行
            stream = self.graph.astream(
                input=inputs,
                config=config,
                subgraphs=True,
                stream_mode="custom", # 采用自定义流式模式，将调用A2A节点的流式输出
            )

            try:
                async for node, chunk in stream:
                    # 停止标志
                    if self.is_stop(session_id):
                        await stream.aclose()
                        break

                    # 文本事件
                    if chunk.get("text", ""):
                        yield make_sse_event(1001, chunk.get("text"))

                    # 工具结果
                    elif chunk.get("tool_result", ""):
                        yield make_sse_event(1003, chunk.get("tool_result"))

            except asyncio.CancelledError:
                # 如果连接被客户端关闭了，则关闭流
                await stream.aclose()
                raise

        except Exception as e:
            if not self.is_stop(session_id): # 停止是正常的，不返回错误
                logger.exception("TJA2AAgent execute error")
                yield make_sse_event(2001, str(e))

        finally:
            # 清除停止标记
            self.reset_stop(session_id)

        # SSE 停止通知
        yield format_sse_data(STOP_EVENT)

    # ----------------------------------------------------------------------------------------
    # 会话相关 API（提供给外部服务）
    # ----------------------------------------------------------------------------------------
    async def session_detail(self, user_id: int, session_id: str):
        """
        查询某个会话的历史消息，用于页面展示。
        """
        config = RunnableConfig(configurable={"thread_id": session_id})
        state_snapshot: StateSnapshot = await self.graph.aget_state(config)
        messages = state_snapshot.values.get("messages", [])

        result = []
        for message in messages:
            msg_type = ""
            content = message.content
            params = {}
            if isinstance(message, HumanMessage):
                msg_type = "USER"
            elif isinstance(message, AIMessage):
                msg_type = "ASSISTANT"
                if message.response_metadata.get("tool_result"):
                    params = message.response_metadata.get("tool_result")

            if msg_type and content:
                result.append({"type": msg_type, "content": content, "params": params})

        return result
    async def delete_session(self, session_id: str):
        """
        删除会话，清除持久化状态
        """
        await self.checkpointer.adelete_thread(session_id)

    def id(self) -> int:
        """返回 agent 类型编号，用于区分不同 Agent"""
        return 1003


# 全局单例实例
tja2a_agent = TJA2AAgent()