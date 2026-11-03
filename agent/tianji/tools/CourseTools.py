import random

from langchain.tools import tool, ToolRuntime

from agent.tianji.tools.result import CourseInfo
from common import *
from config import logger
from util import HttpClientUtil, JsonUtil

"""
流程：
    1. 从 RunnableConfig 中获取用户 token 和 request_id
    2. 通过 Nacos 获取业务网关实例列表
    3. 随机选择一个实例发起 HTTP 请求，获取课程信息
    4. 将返回的数据转换为 CourseInfo 对象
    5. 序列化json返回
"""


@tool
def query_course_by_id(course_id, runtime: ToolRuntime):
    """
    根据课程 ID 查询课程数据，并将结果存储到 ToolResultHolder。

    Args:
        course_id : 课程 ID
        runtime: 获取运行参数
    """
    # 获取必要的配置数据
    user_token = runtime.context.user_token
    request_id = runtime.context.request_id

    # TODO 从 Nacos 获取业务系统网关实例
    url = f"http://127.0.0.1:10010/cs/courses/baseInfo/{course_id}"

    # 发起 HTTP GET 请求获取课程数据
    response_data = HttpClientUtil.get(url, user_token) or {}
    data = response_data.get("data")
    if not data:
        logger.error(f"Failed to fetch course data from {url}")
        return None

    logger.debug("【Tool】 query_course_by_id url=%s, data=%s, request_id=%s", url, data, request_id)
    # 转换为 CourseInfo 对象
    course_info = CourseInfo.of(data)

    # 将结果序列化json，返回给大模型
    return JsonUtil.to_str(course_info)