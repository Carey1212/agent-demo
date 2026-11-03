from abc import ABC, abstractmethod
from async_eval_codex.collection.prompt_context import NODE_TARGETS, record_prompt
from dataclasses import dataclass

from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain_core.messages import SystemMessage
from langchain_core.runnables import RunnableConfig

from agent.tianji.RouteState import RouteState
from config import config_manager


@dataclass
class ToolContext:
    user_token: str
    request_id: str


class BaseNodeAgent(ABC):
    """
    BaseNodeAgent 是一个基础的节点式智能体抽象类，负责：
    - 初始化 LLM（基于配置中心）
    - 创建 LangChain agent（自动挂载工具）
    - 提供统一的系统提示词（由子类决定）
    - 执行消息生成（注入 system prompt + 处理结果）

    该类主要用于 多节点/多阶段 推理流程中的单节点智能体。
    """

    def __init__(self, provider: str = "openai"):
        """
        初始化智能体基础配置与模型。

        :param provider: 模型提供方（默认 openai），将从 config_manager 中读取对应配置。
        """
        cfg = config_manager
        prefix = f"ai.{provider}"

        # 读取模型基础配置
        self.model_name = cfg.get(f"{prefix}.model")
        self.api_key = cfg.get(f"{prefix}.api-key")
        self.base_url = cfg.get(f"{prefix}.base-url")
        self.temperature = float(cfg.get(f"{prefix}.temperature", 0.7))
        self.timeout = int(cfg.get(f"{prefix}.timeout", 60))

        # 初始化 LLM
        self.llm = init_chat_model(
            model=self.model_name,
            model_provider="openai",  # 使用 openai 协议风格的统一接口
            api_key=self.api_key,
            base_url=self.base_url,
            temperature=self.temperature,
            timeout=self.timeout,
            tags=[self.__class__.__name__]  # 为该模型调用添加 tag，用于后续追踪/过滤
        )

        # 创建 LangChain Agent（自动加载 tools）
        self.agent = create_agent(
            model=self.llm,
            tools=self.tools()
        )

    def tools(self):
        """
        返回当前智能体支持的工具列表。

        默认返回空列表，子类可重写此方法以提供工具能力。
        """
        return []

    @abstractmethod
    def system_prompt(self) -> str:
        """
        子类必须实现，返回当前节点智能体所使用的系统提示词。

        返回值必须是一个字符串，可包含 format 参数。
        """
        pass

    def do_result(self, messages):
        """
        处理 agent 返回的最终消息列表。

        默认行为：
        - 返回 {"messages": messages}
        - 子类可根据业务需要重写此方法，提取内容、结构化结果等。

        :param messages: 来自 LangChain agent 的消息（已过滤 system message）
        """
        return {"messages": messages}

    def system_prompt_params(self):
        """
        返回用于填充系统提示词 format 的参数。

        子类可重写该方法向 system prompt 动态注入上下文数据。
        """
        return {}

    async def execute(self, state: RouteState, config: RunnableConfig):
        """
        执行当前节点智能体逻辑。

        流程：
        1. 从 state 取得消息列表
        2. 在最终用户消息前插入系统提示词（不修改原始消息列表）
        3. 使用 LangChain agent 执行推理
        4. 过滤掉 system message
        5. 返回处理后的结果（交由 do_result 控制输出格式）

        :param state: RouteState 对象，包含多节点间的消息流
        :return: 智能体计算结果（默认 {"messages": [...] }）
        """

        # 获取内容（不修改 state 原始消息）
        messages = state["messages"]

        # 为避免污染原消息列表，进行深拷贝
        new_messages = messages.copy()

        # 构建 SystemMessage（允许使用动态参数）
        params = self.system_prompt_params()
        # 保存本次实际读取的模板，避免后台使用热更新后的其他版本。
        template = self.system_prompt()
        record_prompt(NODE_TARGETS.get(type(self).__name__, ""), template)
        system_msg = SystemMessage(template.format(**params))

        # 将 system message 插入到最后一条用户消息前的位置
        # 如果消息为空，则直接添加
        if len(new_messages) >= 1:
            new_messages.insert(-1, system_msg)
        else:
            new_messages.append(system_msg)

        configurable = config.get("configurable", {})
        user_token = configurable.get("user_token", "")
        request_id = configurable.get("request_id", "")

        # 调用智能体执行推理
        res = await self.agent.ainvoke(
            input={"messages": new_messages},
            context=ToolContext(
                user_token=user_token,
                request_id=request_id
            )
        )

        # 从结果中移除系统提示词（不需要存储）
        res_messages = [
            m for m in res["messages"]
            if not isinstance(m, SystemMessage)
        ]

        # 交给子类最终处理
        return self.do_result(res_messages)