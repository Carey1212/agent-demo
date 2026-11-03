"""改进候选的生成、查询、回归、审核与导出入口。"""
import argparse
import asyncio
import json
from pathlib import Path

from ..reflection.evaluator import Evaluator, OpenAIModel
from ..reflection.improvement import Improver
from .regression import RegressionCase, TextReplay, run_regression
from ..collection.repository import Repository
from ..collection.schemas import Snapshot


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("generate", "list"):
        command = commands.add_parser(name, help="从已完成反思生成候选" if name == "generate" else "列出执行的候选")
        command.add_argument("run_id", help="原始执行 ID")
    for name in ("show", "regress", "review", "export"):
        command = commands.add_parser(name)
        command.add_argument("proposal_id", help="候选 ID")
        if name == "regress":
            command.add_argument("--dataset", required=True, help="包含三类用例的 JSON 文件")
        elif name == "review":
            command.add_argument("--decision", choices=["approved", "rejected"], required=True)
            command.add_argument("--current-prompt", help="从目标配置导出的当前原文文件，批准时必填")
            command.add_argument("--reviewer", required=True, help="审核人")
            command.add_argument("--note", required=True, help="审核说明")
        elif name == "export":
            command.add_argument("--output", required=True, help="新文件路径，不覆盖现有文件")
    return parser


async def execute(args, repository):
    if args.command == "generate":
        task = await repository.get(args.run_id)
        if not task or task["status"] not in {"succeeded", "failed"} or not task.get("reflection"):
            raise ValueError("执行仍在处理中或没有可用反思")
        snapshot = Snapshot.model_validate(await repository.get_snapshot(args.run_id))
        return await Improver(repository, OpenAIModel()).improve(snapshot, task["reflection"])
    if args.command == "list":
        return await repository.list_proposals(args.run_id)
    proposal = await repository.get_proposal(args.proposal_id)
    if proposal is None:
        raise ValueError("未找到改进候选")
    if args.command == "show":
        return proposal
    if args.command == "regress":
        data = json.loads(Path(args.dataset).read_text())
        cases = [RegressionCase.model_validate(item) for item in data]
        report = await run_regression(proposal, cases, TextReplay(), Evaluator(OpenAIModel()))
        await repository.save_regression(args.proposal_id, report)
        return report
    if args.command == "review":
        if args.decision == "approved" and not args.current_prompt:
            raise ValueError("批准候选前必须提供当前提示词原文")
        current = Path(args.current_prompt).read_text() if args.current_prompt else ""
        await repository.review_proposal(args.proposal_id, args.decision, current, args.reviewer, args.note)
        return await repository.get_proposal(args.proposal_id)
    if proposal["status"] != "approved":
        raise ValueError("只有已审核通过的候选才能导出")
    # 只输出可供人工发布的文件，不写 Nacos 或修改全局配置。
    try:
        with Path(args.output).open("x") as output:
            output.write(proposal["candidate"])
    except FileExistsError as exc:
        raise ValueError("输出文件已存在，请指定新的文件路径") from exc
    return {"output": args.output, "proposal_id": args.proposal_id}


async def main(args):
    from config import get_async_pg_pool, close_async_pg_pool
    try:
        result = await execute(args, Repository(await get_async_pg_pool()))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        await close_async_pg_pool()


if __name__ == "__main__":
    parser = build_parser()
    try:
        asyncio.run(main(parser.parse_args()))
    except Exception as exc:
        # 不打印 SDK 或数据库错误全文，避免输出连接凭证。
        parser.exit(1, f"改进操作失败，异常类型：{type(exc).__name__}。请检查参数、迁移及服务配置。\n")
