import asyncio
from collections.abc import AsyncIterator

from a2a.client import ClientEvent, A2ACardResolver, ClientFactory, create_text_message_object
from a2a.client.client import Client, ClientConfig
from a2a.types import Message, AgentCard, TransportProtocol
from httpx import AsyncClient, Timeout

from config import config_manager
from common import *

# HTTP 超时配置（用于访问 A2A 服务器）
timeout_config = Timeout(
    timeout=120,  # 整体超时
    connect=10.0,  # 连接超时
    read=120,  # 读取超时
    write=10.0,  # 写入超时
    pool=5.0  # 连接池超时
)

class TJA2AClient:
    """
    TJA2AClient 负责：
    - 从 server 列表中加载所有 Agent 的 AgentCard 信息
    - 基于 AgentCard 创建对应的 Agent 客户端
    - 代理发送消息到对应的 A2A Agent（并返回流式事件）
    """

    def __init__(self, servers, agent_cards: dict[str, AgentCard]):
        """
        :param servers: A2A 服务地址列表
        :param agent_cards: name → AgentCard 映射，用于创建 agent 客户端
        """
        self.servers = servers
        self.agent_cards = agent_cards

    @classmethod
    async def create(cls):
        """
        工厂方法：
        - 从配置中读取 A2A server 列表
        - 请求每个 server 的 AgentCard
        - 组合成统一的 agent_cards 字典

        :return: 初始化后的 TJA2AClient 实例
        """
        servers = config_manager.get(A2A_SERVERS,[])

        agent_cards: dict[str, AgentCard] = {}
        # 向所有 server 获取 AgentCard
        async with AsyncClient(timeout=timeout_config) as httpx_client:
            for server in servers:
                resolver = A2ACardResolver(httpx_client=httpx_client, base_url=server)

                # 每个 server 上可能注册不同的 agent
                agent_card = await resolver.get_agent_card()
                agent_cards[agent_card.name] = agent_card

        return cls(servers, agent_cards)

    def get_agent_client(self, agent_name: str, httpx_client: AsyncClient) -> Client:
        """
        基于 agent 名称获取该 agent 的客户端实例。

        :param agent_name: agent 名
        :param httpx_client: 外部传入的 httpx client，用于共用会话
        :return: 该 agent 的客户端实例
        """

        # 获取 agent card 信息，用于构建 client
        card = self.agent_cards.get(agent_name)

        if card is None:
            raise KeyError(
                f"Agent '{agent_name}' not found. Available: {list(self.agent_cards)}"
            )

        # A2A 客户端配置：支持 JSON-RPC 协议
        config = ClientConfig(
            httpx_client=httpx_client,
            supported_transports=[TransportProtocol.jsonrpc],
        )

        # 基于 config 创建 A2A 客户端工厂
        factory = ClientFactory(config)

        return factory.create(card)

    async def send_message(
            self,
            *,
            question: str,
            request_id: str,
            user_token: str,
            agent_name: str,
            httpx_client: AsyncClient,
            history: list[dict[str, str]] = None,
    ) -> AsyncIterator[ClientEvent | Message]:
        """
        发送消息到指定的 A2A agent，并生成流式事件（类似 OpenAI 的 stream）。

        :param question: 用户输入的问题文本
        :param request_id: 本次请求的唯一 ID
        :param user_token: 用户授权 token
        :param agent_name: 目标 agent 名称
        :param httpx_client: 共用 httpx client，会被 AgentClient 使用
        :param history: 可选历史消息，用于 agent 的上下文
        :return: 异步迭代器，产生 ClientEvent / Message
        """

        # 创建标准文本消息对象（A2A 协议格式）
        message_object = create_text_message_object(content=question)

        # 添加附加元信息（供 downstream agent 使用）
        message_object.metadata = {
            "history": history if history else [],
            "user_token": user_token,
            "request_id": request_id,
        }

        # 获取对应 agent 的客户端实例
        agent_client = self.get_agent_client(agent_name, httpx_client)

        # 将消息发送给 agent，并返回其流式输出
        async for event in agent_client.send_message(message_object):
            yield event


# 创建全局 client（程序加载时异步初始化）
tja2a_client = asyncio.run(TJA2AClient.create())

if __name__ == "__main__":
    """测试：打印加载到的 agent 列表"""
    for name, agent_card in tja2a_client.agent_cards.items():
        print(f"{name}: {agent_card}", end="\n\n")