"""仅在当前请求内记录实际使用的提示词，避免会话之间共享采集状态。"""
from contextvars import ContextVar
from hashlib import sha256

from .schemas import PromptCapture

# LangGraph 创建的异步子任务继承上下文，记录写入当前请求独有的列表。
prompt_captures = ContextVar("eval_prompt_captures", default=None)

NODE_TARGETS = {
    "IntentAgent": "chat_route_message",
    "RecommendAgent": "chat_recommend_message",
    "BuyAgent": "chat_buy_message",
    "ConsultAgent": "chat_consult_message",
    "KnowledgeAgent": "chat_knowledge_message",
    "UnknownAgent": "chat_unknown_message",
}


def record_prompt(target: str, template: str, format_mode: str = "format"):
    """记录原始模板而非带业务参数的渲染结果，返回值不参与 Agent 推理。"""
    captures = prompt_captures.get()
    if captures is None or not target:
        return
    version = sha256(template.encode()).hexdigest()
    if any(item.target == target and item.version == version for item in captures):
        return
    captures.append(PromptCapture(
        target=target, template=template, version=version, format_mode=format_mode
    ))
