"""离线测试：不导入应用配置，不访问数据库或调用模型。"""
import unittest
from async_eval_codex.collection.schemas import Snapshot
from async_eval_codex.collection.service import Collector, observe
from async_eval_codex.reflection.evaluator import Evaluator
from async_eval_codex.execution.worker import process_one
from async_eval_codex.reflection.proposals import make_proposal, compare


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    def snapshot(self, **kwargs):
        return Snapshot(run_id="r", session_id="s", agent_id=1002, question="q", **kwargs)

    async def test_stream_passthrough_and_enqueue(self):
        chunks = ['data: {"eventType":1001,"eventData":"hel', 'lo"}\n\n',
                  'data: {"eventType":1002}\n\n']
        saved = []
        async def source():
            for chunk in chunks:
                yield chunk
        async def submit(snapshot):
            saved.append(snapshot)
        output = [item async for item in observe(source(), "q", "s", 1002, submitter=submit)]
        self.assertEqual(output, chunks)
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].answer, "hello")

    async def test_submit_failure_does_not_break_stream(self):
        async def source():
            yield 'data: {"eventType":1001,"eventData":"ok"}\n\n'
        async def submit(snapshot):
            raise RuntimeError("服务不可用")
        self.assertEqual(len([x async for x in observe(source(), "q", "s", 1, submitter=submit)]), 1)

    async def test_errors_override_model(self):
        async def model(*args):
            self.fail("规则检查失败时不应调用评测模型")
        result = await Evaluator(model).evaluate(self.snapshot(answer="ok", errors=["Timeout"]))
        self.assertEqual(result["status"], "execution_error")
        self.assertTrue(result["reflect"])

    async def test_interruption_skips_grading(self):
        result = await Evaluator(None).evaluate(self.snapshot(status="interrupted"))
        self.assertEqual(result["status"], "inconclusive")
        self.assertFalse(result["reflect"])

    async def test_invalid_score_rejected(self):
        async def model(*args):
            return {key: {"score": 9, "reason": "x"} for key in ["completion", "completeness", "expression"]}
        with self.assertRaises(ValueError):
            await Evaluator(model).evaluate(self.snapshot(answer="ok"))

    async def test_reflection_retry_reuses_evaluation(self):
        class Repo:
            async def claim(inner):
                return ("r", self.snapshot(answer="a").model_dump(), {"reflect": True}, 2, "token")
            async def finish(inner, *args):
                inner.finished = True
        class Judge:
            async def evaluate(inner, snapshot):
                self.fail("必须复用已保存的评测结果")
            async def reflect(inner, *args):
                return {"findings": []}
        repo = Repo()
        self.assertTrue(await process_one(repo, Judge()))
        self.assertTrue(repo.finished)

    def test_candidate_placeholders(self):
        with self.assertRaises(ValueError):
            make_proposal("Hello {name}", "Hello {user}", ["r"])
        self.assertEqual(make_proposal("Hi {name}", "Welcome {name}", ["r"])["status"], "pending_review")

    def test_regression_detected(self):
        result = compare({"a": {"status": "pass"}}, {"a": {"status": "fail"}})
        self.assertEqual(result["regressed"], ["a"])
        self.assertFalse(result["recommend_review"])


if __name__ == "__main__":
    unittest.main()
