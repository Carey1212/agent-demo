"""命令行入口。

用法（在 agent_center 目录下运行）：
    python -m async_eval.cli run                     # 默认配置 + 全部数据集
    python -m async_eval.cli run --dataset sample.yaml --mode http
    python -m async_eval.cli run --no-judge --no-reflect
    python -m async_eval.cli ab --candidate candidates.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from .core import runner
from .core.judge import Judge
from .core.metrics import summarize
from .core.reflect import reflect
from .core.registry import record_experiment
from .core.types import to_dict
from .datasets import load_cases
from .eval_config import DEFAULT_CONFIG_PATH, EvalConfig
from .reporters import console, json_report

def _make_run_id() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def _run_id_safe(cfg: EvalConfig) -> str:
    return cfg.run_id or _make_run_id()


async def _cmd_run(args) -> int:
    cfg = EvalConfig.from_yaml(args.config)
    if args.mode:
        cfg.mode = args.mode
    if args.dataset:
        cfg.datasets = args.dataset
    if args.no_judge:
        cfg.judge = False
    if not cfg.run_id:
        cfg.run_id = _make_run_id()

    cases = load_cases(*cfg.dataset_paths())
    if not cases:
        print("⚠️  没有加载到任何用例。请在 datasets/ 下准备 *.yaml。")
        return 1

    print(f"▶ 运行评测 run_id={cfg.run_id} mode={cfg.mode} 用例数={len(cases)}")
    results = await runner.run(cfg, cases)

    if cfg.judge:
        print("▶ LLM 判分中...")
        await Judge(cfg).judge_all(results)

    summary = summarize(results, cfg.pass_threshold)
    console.print_summary(summary)
    console.print_case_table(results)

    # 低分用例反思
    low = [r for r in results if not r.is_pass(cfg.pass_threshold)]
    reflection = None
    if low and not args.no_reflect:
        print(f"▶ 反思低分用例（{len(low)} 条）...")
        reflection = await reflect(cfg, low)

    run_dir = cfg.resolve_run_dir()
    payload = {
        "run_id": cfg.run_id,
        "config": to_dict(cfg),
        "summary": to_dict(summary),
        "results": [to_dict(r) for r in results],
    }
    report_path = json_report.write_run_report(run_dir, cfg.run_id, payload)
    print(f"📄 报告已写入：{report_path}")

    if reflection:
        rpath = json_report.write_json(
            run_dir / f"reflect_{cfg.run_id}.json", reflection
        )
        print(f"📄 反思报告：{rpath}")
        if reflection["candidates"]:
            cpath = json_report.write_json(
                run_dir / f"candidates_{cfg.run_id}.json", reflection["candidates"]
            )
            print(f"📄 候选 prompt：{cpath}")

    record_experiment(run_dir, {
        "run_id": cfg.run_id,
        "mode": cfg.mode,
        "cases": len(cases),
        "judge_overall_avg": summary.judge_overall_avg,
        "pass_rate": summary.pass_rate,
        "intent_accuracy": summary.intent_accuracy,
        "tool_accuracy": summary.tool_accuracy,
        "error_rate": summary.error_rate,
    })
    return 0


async def _cmd_harvest(args) -> int:
    """从生产会话回流采集评测用例。"""
    from .core.harvest import harvest

    res = await harvest(
        agent_id=args.agent,
        days=args.days,
        limit=args.limit,
        min_turns=args.min_turns,
        max_turns=args.max_turns,
        max_cases=args.max_cases,
        label_points=not args.no_label,
        dry_run=args.dry_run,
    )
    console.print_harvest_summary(res)
    return 0


async def _cmd_ab(args) -> int:
    cfg = EvalConfig.from_yaml(args.config)
    if args.dataset:
        cfg.datasets = args.dataset
    if not cfg.run_id:
        cfg.run_id = _make_run_id()

    candidates = json.loads(Path(args.candidate).read_text(encoding="utf-8"))
    if not isinstance(candidates, dict):
        print("⚠️  candidate 文件必须是 dict：{prompt_name: prompt_text}")
        return 1

    cases = load_cases(*cfg.dataset_paths())
    print(f"▶ A/B 对比 run_id={cfg.run_id} 用例数={len(cases)} 候选prompt={list(candidates)}")

    from .core.ab import run_ab

    result = await run_ab(cfg, cases, candidates)
    console.print_summary(result["baseline"])

    b_avg = result["baseline"].judge_overall_avg
    c_avg = result["candidate"].judge_overall_avg
    print("-" * 60)
    print(f"baseline overall均分 : {b_avg}")
    print(f"candidate overall均分: {c_avg}")
    print(f"结论                 : {result['winner']}")
    print("-" * 60)
    for pc in result["per_case"]:
        print(f"  {pc['case_id']:<26} {pc['baseline_overall']} -> {pc['candidate_overall']}  (Δ{pc['delta']})")

    run_dir = cfg.resolve_run_dir()
    path = json_report.write_json(run_dir / f"ab_{cfg.run_id}.json", result)
    print(f"📄 A/B 报告：{path}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="async_eval", description="天机 AI 评测 + 反思改进")
    sub = parser.add_subparsers(dest="cmd", required=True)

    prun = sub.add_parser("run", help="跑评测 + 判分 + 反思")
    prun.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    prun.add_argument("--dataset", nargs="*", help="数据集文件名，默认 datasets/*.yaml 全部")
    prun.add_argument("--mode", choices=["inprocess", "http"])
    prun.add_argument("--no-judge", action="store_true")
    prun.add_argument("--no-reflect", action="store_true")
    prun.set_defaults(func=_cmd_run)

    pab = sub.add_parser("ab", help="A/B 对比 baseline vs candidate")
    pab.add_argument("--candidate", required=True, help="候选 prompt JSON，{prompt_name: text}")
    pab.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    pab.add_argument("--dataset", nargs="*")
    pab.set_defaults(func=_cmd_ab)

    ph = sub.add_parser("harvest", help="从生产会话回流采集评测用例")
    ph.add_argument("--agent", type=int, default=1001, help="被测 agent（1001/1003）")
    ph.add_argument("--days", type=int, default=7, help="近 N 天的会话")
    ph.add_argument("--limit", type=int, default=500, help="采样的会话数量上限")
    ph.add_argument("--min-turns", type=int, default=1, help="最少轮数，少于则过滤")
    ph.add_argument("--max-turns", type=int, default=4, help="每个用例最多轮数（取最近窗口）")
    ph.add_argument("--max-cases", type=int, default=200, help="最多产出用例数")
    ph.add_argument("--no-label", action="store_true", help="不调用 deepseek 生成 expected_points")
    ph.add_argument("--dry-run", action="store_true", help="只统计不落盘")
    ph.set_defaults(func=_cmd_harvest)

    args = parser.parse_args(argv)
    return asyncio.run(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())