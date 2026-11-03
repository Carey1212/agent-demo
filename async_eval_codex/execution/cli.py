"""按执行 ID 查询已保存的结果：python -m async_eval_codex.execution.cli RUN_ID。"""
import argparse
import asyncio
import json
from ..collection.repository import Repository


async def show(run_id):
    from config import get_async_pg_pool, close_async_pg_pool
    try:
        result = await Repository(await get_async_pg_pool()).get(run_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        await close_async_pg_pool()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    asyncio.run(show(parser.parse_args().run_id))
