"""在隔离执行器中运行两版提示词，保存可追溯的回归依据。"""
import asyncio
from hashlib import sha256
import json
from time import monotonic

from pydantic import BaseModel
from typing import Literal

from ..reflection.proposals import compare
from ..collection.schemas import Snapshot


class RegressionCase(BaseModel):
    # 稳定的用例标识，基线和候选使用同一个 ID。
    case_id: str
    # discovery 用于复现问题，retention 检查原有能力，holdout 为独立验证。
    split: Literal["discovery", "retention", "holdout"]
    # 固定的输入及工具结果，执行器不可访问生产会话。
    snapshot: Snapshot


class TextReplay:
    """重建 TextAgent 的无工具调用方式，不修改线上 Agent 或提示词配置。"""
    target = "chat_text_message"
    name = "isolated_text_agent"

    def __init__(self):
        self.agent = None

    async def __call__(self, prompt, case):
        from langchain.agents import create_agent
        from langchain.chat_models import init_chat_model
        from langchain_core.messages import SystemMessage, HumanMessage
        from config import config_manager
        if case.snapshot.agent_id != 1002 or case.snapshot.tool_results:
            raise ValueError("内置回放仅支持无工具的 TextAgent 用例")
        if self.agent is None:
            model = init_chat_model(
                model=config_manager.get("ai.openai.model"), model_provider="openai",
                api_key=config_manager.get("ai.openai.api-key"),
                base_url=config_manager.get("ai.openai.base-url"),
                temperature=float(config_manager.get("ai.openai.temperature", 0.7)),
                timeout=int(config_manager.get("ai.openai.timeout", 60)),
            )
            self.agent = create_agent(model=model)
        result = await self.agent.ainvoke({"messages": [SystemMessage(prompt), HumanMessage(case.snapshot.question)]})
        content = result["messages"][-1].content
        if not isinstance(content, str):
            raise ValueError("回放输出不是纯文本，请提供支持该内容类型的执行器")
        return case.snapshot.model_copy(update={
            "answer": content, "status": "completed", "errors": [], "tool_results": [], "prompts": []
        })


async def run_regression(proposal, cases, replay, evaluator):
    """replay 是隔离的异步执行器，接收提示词与用例，返回本次执行快照。"""
    if proposal.get("kind") != "prompt" or proposal.get("status") != "pending_review":
        raise ValueError("只能回归尚未审核的提示词候选")
    if getattr(replay, "target", None) != proposal["target"]:
        raise ValueError("回放执行器与候选目标不匹配")
    if not cases or len({item.case_id for item in cases}) != len(cases):
        raise ValueError("回归用例不能为空，且用例 ID 不得重复")
    if {item.split for item in cases} != {"discovery", "retention", "holdout"}:
        raise ValueError("回归必须包含问题复现、原有能力和独立验证三类用例")
    # 同一问题改名后不能伪装成独立验证；这里做精确检查，语义独立性仍需审核。
    questions = [item.snapshot.question.strip() for item in cases]
    if len(set(questions)) != len(questions):
        raise ValueError("回归用例的问题不得重复")
    for case in cases:
        if case.snapshot.agent_id != proposal["agent_id"]:
            raise ValueError("回归用例与候选的 Agent 不一致")
        if case.split == "holdout" and (
            case.snapshot.run_id in proposal["case_ids"]
            or case.snapshot.question.strip() in proposal.get("source_questions", [])
        ):
            raise ValueError("生成候选使用的案例不能作为独立验证集")
    for key, text in (("base_hash", proposal["original"]), ("candidate_hash", proposal["candidate"])):
        if sha256(text.encode()).hexdigest() != proposal[key]:
            raise ValueError("候选内容与保存的版本不一致")

    baseline, candidate, executions = {}, {}, {}
    for index, case in enumerate(cases):
        pair = {}
        # 交替两版的调用次序，降低固定次序带来的时间偏差。
        variants = [("baseline", proposal["original"]), ("candidate", proposal["candidate"])]
        if index % 2:
            variants.reverse()
        for label, prompt in variants:
            started = monotonic()
            try:
                async with asyncio.timeout(180):
                    snapshot = Snapshot.model_validate(await replay(prompt, case.model_copy(deep=True)))
                    if snapshot.question != case.snapshot.question or snapshot.agent_id != case.snapshot.agent_id:
                        raise ValueError("回放执行器修改了用例问题或 Agent")
                    result = await evaluator.evaluate(snapshot)
                pair[label] = {"snapshot": snapshot.model_dump(), "evaluation": result,
                               "elapsed_ms": (monotonic() - started) * 1000}
            except Exception as exc:
                result = {"status": "execution_error", "issues": [f"回归执行失败：{type(exc).__name__}"]}
                pair[label] = {"evaluation": result, "elapsed_ms": (monotonic() - started) * 1000}
            (baseline if label == "baseline" else candidate)[case.case_id] = result
        executions[case.case_id] = {"split": case.split, **pair}

    comparison = compare(baseline, candidate)
    fixed_discovery = any(item.split == "discovery" and item.case_id in comparison["fixed"] for item in cases)
    preserved = all(baseline[item.case_id]["status"] == candidate[item.case_id]["status"] == "pass"
                    for item in cases if item.split == "retention")
    holdout_passed = all(candidate[item.case_id]["status"] == "pass" for item in cases if item.split == "holdout")
    comparison["recommend_review"] = bool(
        comparison["recommend_review"] and fixed_discovery and preserved and holdout_passed
    )
    dataset = json.dumps([item.model_dump() for item in cases], ensure_ascii=False, sort_keys=True)
    return {
        **comparison, "proposal_id": proposal["proposal_id"], "base_hash": proposal["base_hash"],
        "candidate_hash": proposal["candidate_hash"], "dataset_hash": sha256(dataset.encode()).hexdigest(),
        "adapter": replay.name, "executions": executions,
        "checks": {"fixed_discovery": fixed_discovery, "preserved": preserved, "holdout_passed": holdout_passed},
    }
