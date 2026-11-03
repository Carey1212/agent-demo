"""改进闭环的离线测试，不连接数据库、Agent 服务或模型服务。"""
import asyncio
from copy import deepcopy
from hashlib import sha256
import unittest

from async_eval_codex.reflection.evaluator import Evaluator
from async_eval_codex.reflection.improvement import Improver, validate_review
from async_eval_codex.collection.prompt_context import record_prompt, prompt_captures
from async_eval_codex.reflection.proposals import make_proposal, placeholders
from async_eval_codex.execution.regression import RegressionCase, run_regression
from async_eval_codex.collection.schemas import Snapshot, PromptCapture
from async_eval_codex.collection.service import observe
from async_eval_codex.execution.worker import process_one


def snapshot(run_id="r", prompt="原始提示词"):
    return Snapshot(run_id=run_id, session_id="s", agent_id=1002, question="问题复现", answer="错误回答",
                    prompts=[PromptCapture(target="chat_text_message", template=prompt,
                                           version=sha256(prompt.encode()).hexdigest(), format_mode="literal")])


def reflection(action="prompt", target="chat_text_message", confidence="high"):
    return {"findings": [{"action": action, "target": target, "category": "generation",
                          "problem": "遗漏要求", "evidence": ["answer"], "hypothesis": "指令不明确",
                          "confidence": confidence, "suggestion": "明确要求", "verification": "回归验证"}]}


class MemoryRepository:
    """模拟持久化边界，验证重试后的候选复用和阶段调用顺序。"""
    def __init__(self):
        self.proposals = {}
        self.evaluation = None
        self.reflection = None
        self.failures = []
        self.finished = False

    async def get_proposal(self, proposal_id):
        return self.proposals.get(proposal_id)

    async def save_proposal(self, proposal, lease_token=None):
        self.proposals.setdefault(proposal["proposal_id"], deepcopy(proposal))
        return self.proposals[proposal["proposal_id"]]

    async def claim(self, **kwargs):
        return "r", snapshot().model_dump(), self.evaluation, 1, "token"

    async def get(self, run_id):
        return {"reflection": self.reflection}

    async def save_evaluation(self, run_id, token, evaluation):
        self.evaluation = evaluation
        return True

    async def save_reflection(self, run_id, token, value):
        self.reflection = value
        return True

    async def finish(self, *args):
        self.finished = True

    async def fail(self, *args):
        self.failures.append(args)


class ImprovementTests(unittest.IsolatedAsyncioTestCase):
    async def test_candidate_persisted_and_reused(self):
        calls = []
        async def model(*args):
            calls.append(args)
            return {"candidate": "改进提示词"}
        repository = MemoryRepository()
        improver = Improver(repository, model)
        first = await improver.improve(snapshot(), reflection())
        second = await improver.improve(snapshot(), reflection())
        self.assertEqual(first, second)
        self.assertEqual(len(calls), 1)
        self.assertEqual(first[0]["target"], "chat_text_message")
        self.assertEqual(first[0]["original"], "原始提示词")

    async def test_worker_evaluation_reflection_improvement_pipeline(self):
        calls = []
        async def model(instruction, data):
            calls.append(data)
            if "snapshot" in data:
                return reflection()
            if "original" in data:
                self.assertEqual(data["context"]["answer"], "错误回答")
                self.assertEqual(data["context"]["question"], "问题复现")
                return {"candidate": "改进提示词"}
            return {
                **{key: {"score": 2, "reason": "遗漏要求", "evidence": ["answer"]}
                   for key in ("completion", "completeness", "expression")},
                "issues": ["遗漏要求"], "insufficient_evidence": False,
            }
        repository = MemoryRepository()
        await process_one(repository, Evaluator(model), Improver(repository, model))
        self.assertTrue(repository.finished)
        self.assertEqual(repository.evaluation["status"], "fail")
        proposal = next(iter(repository.proposals.values()))
        self.assertEqual(proposal["candidate"], "改进提示词")
        self.assertEqual(proposal["status"], "pending_review")
        self.assertEqual(len(calls), 3)
        self.assertEqual(repository.failures, [])

    async def test_invalid_candidate_not_persisted(self):
        async def model(*args):
            return {"candidate": "改进提示词 {wrong}"}
        value = snapshot(prompt="原始提示词 {name}")
        value.prompts[0].format_mode = "format"
        repository = MemoryRepository()
        with self.assertRaisesRegex(ValueError, "占位符"):
            await Improver(repository, model).improve(value, reflection())
        self.assertEqual(repository.proposals, {})

    async def test_unconfirmed_findings_do_not_call_model(self):
        async def model(*args):
            self.fail("不应为缺少依据的建议生成提示词")
        for value in [reflection("tool"), reflection(target="missing"), reflection(confidence="low")]:
            result = await Improver(MemoryRepository(), model).improve(snapshot(), value)
            self.assertEqual(result[0]["kind"], "manual")

    async def test_missing_and_mixed_prompt_versions_require_investigation(self):
        async def model(*args):
            self.fail("不应选择不确定的提示词版本")
        empty = snapshot().model_copy(update={"prompts": []})
        mixed = snapshot()
        mixed.prompts.extend(snapshot(prompt="另一版本").prompts)
        for value in [empty, mixed]:
            result = await Improver(MemoryRepository(), model).improve(value, reflection())
            self.assertEqual(result[0]["status"], "needs_investigation")

    async def test_worker_retries_only_failed_improvement(self):
        repository = MemoryRepository()
        class Judge:
            async def evaluate(inner, value):
                if repository.evaluation:
                    self.fail("不应重复评测")
                return {"reflect": True}
            async def reflect(inner, *args):
                if repository.reflection:
                    self.fail("不应重复反思")
                return reflection()
        calls = 0
        async def model(*args):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise TimeoutError("模拟超时")
            return {"candidate": "改进提示词"}
        improver = Improver(repository, model)
        await process_one(repository, Judge(), improver)
        self.assertEqual(len(repository.failures), 1)
        self.assertIsNotNone(repository.reflection)
        await process_one(repository, Judge(), improver)
        self.assertTrue(repository.finished)
        self.assertEqual(len(repository.proposals), 1)

    async def test_prompt_capture_isolated_between_requests(self):
        saved = []
        async def source(text):
            await asyncio.sleep(0)
            record_prompt("chat_text_message", text, "literal")
            yield 'data: {"eventType":1001,"eventData":"回答"}\n\n'
        async def submit(value):
            saved.append(value)
        async def consume(text):
            async for _ in observe(source(text), "问题", text, 1002, submitter=submit):
                self.assertIsNone(prompt_captures.get())
        await asyncio.gather(consume("甲"), consume("乙"))
        self.assertEqual({item.session_id: item.prompts[0].template for item in saved}, {"甲": "甲", "乙": "乙"})
        self.assertTrue(all("prompt_version" not in item.missing_evidence for item in saved))

    def test_template_validation_and_literal_braces(self):
        with self.assertRaises(ValueError):
            make_proposal("{value:{width}}", "改进 {value:{height}}", ["r"])
        with self.assertRaises(ValueError):
            placeholders("未闭合 {")
        with self.assertRaises(ValueError):
            make_proposal("相同", "相同", ["r"])
        result = make_proposal('返回 {"a":1}', '必须返回 {"a":1}', ["r"], "literal")
        self.assertEqual(result["format_mode"], "literal")

    async def proposal(self):
        async def model(*args):
            return {"candidate": "改进提示词"}
        return (await Improver(MemoryRepository(), model).improve(snapshot(), reflection()))[0]

    def cases(self):
        return [RegressionCase(case_id=str(index), split=split,
                               snapshot=snapshot(str(index)).model_copy(update={"question": question}))
                for index, (split, question) in enumerate([
                    ("discovery", "问题复现"), ("retention", "原有能力"), ("holdout", "独立问题")])]

    async def test_regression_and_review(self):
        proposal = await self.proposal()
        class Replay:
            target = "chat_text_message"
            name = "mock"
            async def __call__(inner, prompt, case):
                passed = prompt == "改进提示词" or case.split != "discovery"
                return case.snapshot.model_copy(update={"answer": "正确" if passed else "错误"})
        class Judge:
            async def evaluate(inner, value):
                return {"status": "pass" if value.answer == "正确" else "fail"}
        report = await run_regression(proposal, self.cases(), Replay(), Judge())
        self.assertTrue(report["recommend_review"])
        validate_review(proposal, report, proposal["original"])
        with self.assertRaisesRegex(ValueError, "已经变化"):
            validate_review(proposal, report, "线上新版")
        with self.assertRaisesRegex(ValueError, "不匹配"):
            validate_review(proposal, {**report, "candidate_hash": "旧版本"}, proposal["original"])

    async def test_regression_error_blocks_review(self):
        proposal = await self.proposal()
        class Replay:
            target = "chat_text_message"
            name = "mock"
            async def __call__(inner, *args):
                raise TimeoutError("模拟失败")
        report = await run_regression(proposal, self.cases(), Replay(), None)
        self.assertFalse(report["recommend_review"])
        self.assertEqual(len(report["uncertain"]), 3)

    async def test_holdout_must_be_independent(self):
        proposal = await self.proposal()
        cases = self.cases()
        cases[-1].snapshot.run_id = "r"
        class Replay:
            target = "chat_text_message"
        with self.assertRaisesRegex(ValueError, "独立验证"):
            await run_regression(proposal, cases, Replay(), None)


if __name__ == "__main__":
    unittest.main()
