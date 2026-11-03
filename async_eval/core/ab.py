"""A/B 对比：baseline（当前 prompt） vs candidate（候选 prompt），进程内运行 + 判分。

通过临时覆盖 system_prompt_config 的对应属性实现 candidate prompt 注入，
不改动 Nacos、不改现有文件。仅用于评测对比，确认提升后再决定是否发布到 Nacos。
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from typing import Any

from ..eval_config import EvalConfig
from . import runner
from .judge import Judge
from .metrics import summarize
from .reflect import PROMPT_ATTRS
from .types import EvalResult


@contextmanager
def override_prompts(overrides: dict[str, str]):
    """临时覆盖 system_prompt_config 的 prompt 属性，退出后还原。"""
    from agent.prompts import system_prompt_config

    backup: dict[str, str] = {}
    try:
        for name, text in overrides.items():
            attr = PROMPT_ATTRS[name]
            backup[attr] = getattr(system_prompt_config, attr, "")
            setattr(system_prompt_config, attr, text)
        yield
    finally:
        for attr, val in backup.items():
            setattr(system_prompt_config, attr, val)


async def _run_and_judge(cfg: EvalConfig, cases) -> dict[str, Any]:
    results: list[EvalResult] = await runner.run(cfg, cases)
    if cfg.judge:
        await Judge(cfg).judge_all(results)
    return {
        "summary": summarize(results, cfg.pass_threshold),
        "results": results,
    }


async def run_ab(
    cfg: EvalConfig,
    cases,
    candidate_overrides: dict[str, str],
) -> dict[str, Any]:
    """返回 baseline 与 candidate 两组的汇总 + 逐用例对比。"""
    baseline = await _run_and_judge(cfg, cases)

    with override_prompts(candidate_overrides):
        candidate = await _run_and_judge(cfg, cases)

    per_case = []
    for b, c in zip(baseline["results"], candidate["results"]):
        bo = b.judge.get("overall") if b.judge else None
        co = c.judge.get("overall") if c.judge else None
        per_case.append(
            {
                "case_id": b.case.case_id,
                "baseline_overall": bo,
                "candidate_overall": co,
                "delta": (round(co - bo, 2) if (bo is not None and co is not None) else None),
            }
        )

    def _avg(s):
        return s.judge_overall_avg

    winner = "tie"
    if _avg(baseline["summary"]) is not None and _avg(candidate["summary"]) is not None:
        if _avg(candidate["summary"]) > _avg(baseline["summary"]):
            winner = "candidate"
        elif _avg(candidate["summary"]) < _avg(baseline["summary"]):
            winner = "baseline"

    return {
        "baseline": baseline["summary"],
        "candidate": candidate["summary"],
        "winner": winner,
        "per_case": per_case,
    }