"""将反思转成持久化改进候选；不会自动修改业务代码或线上配置。"""
from hashlib import sha256
import json

from .proposals import generate_proposal
from ..collection.schemas import Reflection


class Improver:
    def __init__(self, repository, model):
        self.repository = repository
        self.model = model

    async def improve(self, snapshot, reflection, lease_token=None):
        """按修改目标分组，逐个保存候选；已保存的候选在重试时直接复用。"""
        findings = Reflection.model_validate(reflection).findings
        groups = {}
        for finding in findings:
            # 没有明确目标、低可信或非 Prompt 问题，都保存为人工处理建议。
            key = finding.target if finding.action == "prompt" and finding.confidence != "low" else None
            groups.setdefault(key, []).append(finding.model_dump())

        results = []
        for target, items in groups.items():
            captures = [item for item in snapshot.prompts if item.target == target]
            versions = {(item.version, item.format_mode) for item in captures}
            usable = target is not None and len(versions) == 1 and bool(captures)
            base = captures[0] if usable else None
            if base and sha256(base.template.encode()).hexdigest() != base.version:
                raise ValueError("快照中的提示词版本与原文不一致")
            # 相同执行、目标、证据和版本生成稳定 ID，模型重试不会新增重复候选。
            identity = json.dumps([snapshot.run_id, target, (base.version, base.format_mode) if base else None, items],
                                  ensure_ascii=False, sort_keys=True)
            proposal_id = sha256(identity.encode()).hexdigest()
            existing = await self.repository.get_proposal(proposal_id)
            if existing:
                results.append(existing)
                continue

            proposal = {
                "proposal_id": proposal_id, "run_id": snapshot.run_id,
                "agent_id": snapshot.agent_id, "target": target,
                "findings": items, "case_ids": [snapshot.run_id],
                "source_questions": [snapshot.question.strip()],
            }
            if base:
                proposal.update(await generate_proposal(
                    self.model, base.template, items, [snapshot.run_id], base.format_mode,
                    context={
                        "question": snapshot.question, "answer": snapshot.answer,
                        "tool_results": snapshot.tool_results, "errors": snapshot.errors,
                        "status": snapshot.status, "missing_evidence": snapshot.missing_evidence,
                    },
                ))
                proposal["kind"] = "prompt"
            else:
                proposal.update({
                    "kind": "manual", "status": "needs_investigation",
                    "reason": "需要确认修改位置或原因；缺少唯一提示词版本的建议不自动生成候选。",
                })
            saved = await self.repository.save_proposal(proposal, lease_token)
            results.append(saved)
        return results


def validate_review(proposal, regression, current_prompt):
    """审核前检查原始版本和回归绑定，审批不等于发布。"""
    if proposal.get("kind") != "prompt":
        raise ValueError("人工处理建议不能作为提示词候选审批")
    if sha256(current_prompt.encode()).hexdigest() != proposal["base_hash"]:
        raise ValueError("当前提示词已经变化，请基于新版本重新生成候选")
    if not regression or not regression.get("recommend_review"):
        raise ValueError("候选尚未通过回归验证")
    for field in ("proposal_id", "base_hash", "candidate_hash"):
        if regression.get(field) != proposal.get(field):
            raise ValueError("回归报告与候选版本不匹配")
