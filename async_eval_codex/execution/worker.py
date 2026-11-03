"""执行数据库迁移后，使用 python -m async_eval_codex.execution.worker 启动后台任务处理器。"""
import asyncio
import logging
import os
from ..collection.schemas import Snapshot
from ..collection.repository import Repository
from ..reflection.evaluator import Evaluator, OpenAIModel

logger = logging.getLogger(__name__)


async def process_one(repository, evaluator, improver=None):
    # 开启改进时，为多个节点的候选生成预留时间，已保存的阶段可重试复用。
    job = await repository.claim(lease_seconds=600) if improver else await repository.claim()
    if job is None:
        return False
    run_id, payload, evaluation, attempts, token = job
    try:
        # 限制评测、反思及候选生成的总处理时间，使其短于租约。
        async with asyncio.timeout(540 if improver else 140):
            snapshot = Snapshot.model_validate(payload)
            if evaluation is None:
                evaluation = await evaluator.evaluate(snapshot)
                if not await repository.save_evaluation(run_id, token, evaluation):
                    return True
            reflection = None
            if evaluation["reflect"]:
                previous = await repository.get(run_id) if improver else None
                reflection = previous.get("reflection") if previous else None
                if reflection is None:
                    reflection = await evaluator.reflect(snapshot, evaluation)
                    if improver and not await repository.save_reflection(run_id, token, reflection):
                        return True
                if improver:
                    await improver.improve(snapshot, reflection, lease_token=token)
            await repository.finish(run_id, token, reflection)
    except Exception as exc:
        # 仅保存异常类型，避免将 SDK 错误中可能包含的凭证写入数据库。
        logger.warning("评测任务处理失败，执行 ID：%s，异常类型：%s", run_id, type(exc).__name__)
        await repository.fail(run_id, token, attempts, f"评测任务处理失败：{type(exc).__name__}")
    return True


async def main():
    from config import get_async_pg_pool, close_async_pg_pool
    repository = Repository(await get_async_pg_pool())
    model = OpenAIModel()
    evaluator = Evaluator(model)
    improver = None
    if os.getenv("AGENT_IMPROVEMENT_ENABLED", "false").lower() == "true":
        from ..reflection.improvement import Improver
        improver = Improver(repository, model)
    try:
        while True:
            try:
                if not await process_one(repository, evaluator, improver):
                    await asyncio.sleep(1)
            except Exception as exc:
                logger.warning("任务队列不可用，异常类型：%s", type(exc).__name__)
                await asyncio.sleep(5)
    finally:
        await close_async_pg_pool()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
