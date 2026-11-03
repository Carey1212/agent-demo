from fastapi import FastAPI, Request
from starlette.responses import PlainTextResponse
from agent import AGENTS

from web.routers import *
from config import get_async_pg_pool, close_async_pg_pool, logger, nacos_config, config_manager
from common import *

from .RequestFilter import RequestFilter

# ========================= 创建 FastAPI 实例 =========================
app = FastAPI(
    title="Agent Center Web Server",
    description="黑马程序员智能体中心"
)

# ========================= 中间件 =========================
# 添加请求过滤器，对请求来源和 token 的有效性进行校验
# app.add_middleware(RequestFilter)

# ========================= 异常处理 =========================
def system_exception_handler(req: Request, exc: Exception):
    """
    全局异常处理函数，将异常转换为 500 响应
    """
    return PlainTextResponse(
        content=str(exc),
        status_code=500
    )

# ========================= Nacos 注册与注销 =========================
def register_service():
    """
    注册智能体中心服务到 Nacos 服务发现
    """
    client = nacos_config.get_discovery_client()  # 获取 Nacos 注册客户端
    ip = nacos_config.get_discovery_ip()  # 服务 IP
    service_name = nacos_config.get_discovery_name()  # 服务名称
    port = int(config_manager.get(SERVER_PORT))  # 服务端口
    group_name = nacos_config.get_discovery_group()  # 分组名称

    # 将实例注册到 Nacos
    result = client.add_naming_instance(
        service_name=service_name,
        ip=ip,
        port=port,
        group_name=group_name,
        heartbeat_interval=10  # 心跳间隔 10 秒
    )
    logger.info(f"✅ Registered {service_name} to Nacos: {result}")
    return result


def deregister_service():
    """
    注销智能体中心服务
    """
    ip = nacos_config.get_discovery_ip()
    service_name = nacos_config.get_discovery_name()
    port = int(config_manager.get(SERVER_PORT))

    # 从 Nacos 注销实例
    result = nacos_config.get_discovery_client().remove_naming_instance(
        service_name, ip, port
    )
    logger.info(f"🧹 Deregistered {service_name} from Nacos")
    return result

# 添加全局异常处理器
app.add_exception_handler(Exception, system_exception_handler)

# ========================= 启动事件 =========================
async def startup():
    """
    启动 web 服务时执行：
    - 初始化所有 Agent
    """

    # 初始化异步数据库连接池
    await get_async_pg_pool()

    # 初始化所有 Agent
    for agent in AGENTS.values():
        await agent.init()

    # 注册服务
    register_service()

# ========================= 关闭事件 =========================
async def shutdown():
    """
    停止 web 服务时执行：
    - 销毁所有 Agent
    """

    # 关闭数据库连接池
    await close_async_pg_pool()

    # 销毁所有 Agent
    for agent in AGENTS.values():
        await agent.destroy()

    # 注销服务
    deregister_service()

# ========================= 事件注册 =========================
# 启动事件：初始化数据库、Agents、注册服务
app.add_event_handler("startup", startup)
# 关闭事件：关闭资源、注销服务
app.add_event_handler("shutdown", shutdown)

# ========================= 路由注册 =========================
# 将各个子路由模块挂载到不同前缀下
app.include_router(auth_router, prefix="/auth", tags=["auth"])
app.include_router(session_router, prefix="/session", tags=["session"])
app.include_router(chat_router, prefix="/chat", tags=["chat"])