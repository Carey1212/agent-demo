"""采集、评测和反思共用的数据结构及校验规则。"""
from typing import Literal
from pydantic import BaseModel, Field


class PromptCapture(BaseModel):
    # 对应 SystemPromptConfig 中的属性名，标识实际执行的节点。
    target: str
    # 执行时读取的原始系统提示词，保留格式占位符。
    template: str
    # 原始模板的 SHA-256，用于版本一致性检查。
    version: str
    # format 表示节点会渲染模板；literal 表示原文直接交给模型。
    format_mode: Literal["format", "literal"] = "format"


class Snapshot(BaseModel):
    # 本次 Agent 执行的唯一标识，同时作为评测任务的去重键。
    run_id: str
    # 所属会话的标识；同一会话中的多次执行具有不同的 run_id。
    session_id: str
    # 被评测的 Agent 标识，用于关联具体智能体。
    agent_id: int
    # 本轮用户输入的问题，不包含完整历史对话。
    question: str
    # 从 SSE 文本事件中逐段拼接的回答，中断时可能只有部分内容。
    answer: str = ""
    # SSE 中返回的工具结果，按接收顺序保存，不代表完整工具调用轨迹。
    tool_results: list[dict] = Field(default_factory=list)
    # 采集到的执行错误或 SSE 解析错误，供规则检查和反思使用。
    errors: list[str] = Field(default_factory=list)
    # Agent 执行状态：正常结束、被中断或执行异常，不是评测任务的处理状态。
    status: Literal["completed", "interrupted", "execution_error"] = "completed"
    # 从开始消费输出流到结束检查完成的耗时，单位毫秒，不含后台评测耗时。
    duration_ms: float = 0
    # 本次执行实际读取的提示词；旧快照默认空列表。
    prompts: list[PromptCapture] = Field(default_factory=list)
    # 当前未采集的证据类型，提示评测模型避免推测历史、参数、轨迹和提示词版本。
    missing_evidence: list[str] = Field(default_factory=lambda: [
        "conversation_history", "tool_arguments", "node_trace", "prompt_version"
    ])


class Assessment(BaseModel):
    # 单个维度的评分，只接受 1 至 5 的整数，分数越高表示表现越好。
    score: int = Field(ge=1, le=5, strict=True)
    # 该维度的评分理由，不能为空。
    reason: str = Field(min_length=1)
    # 支持评分的字段路径或原文片段；当前允许为空，未校验证据路径是否存在。
    evidence: list[str] = Field(default_factory=list)


class Judgment(BaseModel):
    # 任务完成度：回答是否解决了用户本轮提出的诉求。
    completion: Assessment
    # 事实一致性：回答是否符合工具结果；缺少可核验依据时可为 None。
    consistency: Assessment | None = None
    # 内容完整性：回答是否覆盖用户要求的关键内容。
    completeness: Assessment
    # 表达质量：回答是否清晰、相关且无不必要的冗余。
    expression: Assessment
    # 模型发现的具体问题；列表非空时，当前评测逻辑会判定失败并触发反思。
    issues: list[str] = Field(default_factory=list)
    # 是否缺少可靠判断所需的证据；未判定失败时，此标记使结果成为 inconclusive。
    insufficient_evidence: bool = False


class Finding(BaseModel):
    # 建议修改方式；旧反思默认为待调查，不自动转成提示词修改。
    action: Literal["prompt", "code", "tool", "knowledge", "investigate"] = "investigate"
    # 提示词修改目标，必须对应快照 prompts 中的 target；其他建议可留空。
    target: str | None = None
    # 问题类别：意图、上下文、知识、工具、外部服务、回答生成或评测本身。
    category: Literal["intent", "context", "knowledge", "tool", "service", "generation", "evaluation"]
    # 已观察到的具体问题描述，不能为空。
    problem: str = Field(min_length=1)
    # 支持该问题的字段路径或原文片段，至少提供一条。
    evidence: list[str] = Field(min_length=1)
    # 对问题成因的假设，不代表已经确认的根因。
    hypothesis: str = Field(min_length=1)
    # 模型对原因假设的可信程度：低、中或高，并非经过统计校准的概率。
    confidence: Literal["low", "medium", "high"]
    # 针对问题提出的具体改进建议，仅保存建议，不自动执行修改。
    suggestion: str = Field(min_length=1)
    # 验证原因假设或改进效果的方法，例如补充观测或执行回归用例。
    verification: str = Field(min_length=1)


class Reflection(BaseModel):
    # 本次反思产生的问题分析集合，至少包含一条 Finding。
    findings: list[Finding] = Field(min_length=1)
