from async_eval_codex.collection.prompt_context import record_prompt
from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage, HumanMessage

from agent.BaseAgent import *
from config import config_manager, logger
from typing import Optional
from agent.prompts import system_prompt_config



class TextAgent(BaseAgent):
    """
    TextAgent：通用文本生成智能体（非路由型）。

    功能：
    - 纯粹执行系统提示词 + 用户输入
    - 不依赖多节点 Graph，不需要状态管理
    - 使用 LangChain agent.stream 实现流式输出
    - 适用于通用文本解释、随问随答等简单场景
    """

    def __init__(self, provider: str = "openai"):
        self.provider = provider
        self.llm: Optional[BaseChatModel] = None  # LLM 实例
        self.agent: Optional[BaseAgent] = None  # LangChain agent 实例

    async def init(self):
        """
        初始化 TextAgent：
        1. 从配置读取模型参数
        2. 创建 LLM 实例
        3. 创建 LangChain Agent（无工具）
        """
        cfg = config_manager
        prefix = f"ai.{self.provider}"

        # 加载模型配置
        model_name = cfg.get(f"{prefix}.model")
        api_key = cfg.get(f"{prefix}.api-key")
        base_url = cfg.get(f"{prefix}.base-url")
        temperature = float(cfg.get(f"{prefix}.temperature", 0.7))
        timeout = int(cfg.get(f"{prefix}.timeout", 60))

        # 初始化 LLM（统一走 openai-compatible 接口）
        self.llm = init_chat_model(
            model=model_name,
            model_provider="openai",
            api_key=api_key,
            base_url=base_url,
            temperature=temperature,
            timeout=timeout
        )

        # 创建一个无工具的通用智能体
        self.agent = create_agent(
            model=self.llm
        )

    async def execute(self, question: str, session_id: str, user_token: str) -> AsyncIterable[str]:
        """
        TextAgent 的主执行方法：

        流程：
        1. 构建 SystemMessage + HumanMessage
        2. 调用 agent.stream 产生流式生成内容
        3. 逐段转换为 SSE 事件推送至前端
        4. 不参与会话状态保存（无 checkpointer）
        """
        try:
            # System + 用户消息结构
            # system_prompt_text = """
            # 角色
            # 你是一名非常出色的IT行业的内容创作者，你的任务是负责内容的帮写、续写、润色和精简。你的目标是帮助学员完成内容的创作，确保内容的合理性。
            #
            # 技能
            # 技能 1: 内容帮写
            # 1. 基于用户提供的主题/关键词，智能生成完整的文案内容，帮助用户快速搭建内容框架。
            #
            # 技能 2: 内容续写
            # 1. 在用户已有文本基础上，自动延续写作思路生成后续内容，保持上下文逻辑连贯性。
            #
            # 技能 3: 内容润色
            # 1. 对现有文本进行语言优化，包括调整句式结构、替换精准词汇、统一行文风格等
            #
            # 技能 4: 内容精简
            # 1. 通过语义分析智能提炼核心信息，删除冗余表达，将长文本压缩为简洁版本
            #
            # 限制:
            # - AI创作必须严格遵循法律法规和伦理准则，禁止生成危害国家安全、宣扬恐怖极端思想、传播虚假谣言、侵犯他人隐私及知识产权的内容，不得涉及暴力色情、种族宗教歧视、历史虚无主义等违背公序良俗的表述，同时要特别注意避免教唆犯罪、诱导危险行为、损害未成年人身心健康，并在医疗、金融、新闻等专业领域确保内容真实性和安全性，始终以社会主义核心价值观为框架，履行技术向善的社会责任。
            # """
            # TextAgent 直接使用原文，不执行 str.format。
            template = system_prompt_config.chat_text_message
            record_prompt("chat_text_message", template, "literal")
            prompts = [
                SystemMessage(template),
                HumanMessage(question)
            ]

            # 流式生成，返回 (token, metadata)
            res = self.agent.stream(
                input={"messages": prompts},
                stream_mode="messages"  # 以 message token 形式流式返回
            )

            # 输出内容 token
            for token, metadata in res:
                if token.content:
                    yield make_sse_event(1001, token.content)

        except Exception as e:
            # 捕获并打印异常，不终止 SSE 流
            logger.error(f"❌ TextAgent Error: {e}")
            yield make_sse_event(2001, str(e))

        # 推送 SSE 停止事件，表示内容发送完毕
        yield format_sse_data(STOP_EVENT)

    def id(self) -> int:
        """TextAgent 的唯一 ID，用于系统标识"""
        return 1002

    # --- 以下两个方法 TextAgent 不支持，会话不需要状态保存 ---
    def session_detail(self, user_id: int, session_id: str) -> list:
        """TextAgent 不进行会话持久化，因此无需实现 session_detail"""
        raise NotImplementedError("暂不提供实现.")

    def delete_session(self, session_id: str):
        """TextAgent 不具备持久化能力，因此无需删除会话"""
        raise NotImplementedError("暂不提供实现.")


# 全局 TextAgent 实例
text_agent = TextAgent()