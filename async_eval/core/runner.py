"""并发跑批：按用例并发采集，返回 EvalResult 列表。"""
from __future__ import annotations

import asyncio

from ..eval_config import EvalConfig
from . import context
from .collector import Collector
from .http_collector import HttpCollector
from .types import EvalCase, EvalResult, RuleMetrics, Transcript


def compute_rule_metrics(case: EvalCase, tr) -> RuleMetrics:
    m = RuleMetrics()
    m.stream_ok = bool(tr.stopped) and len(tr.errors) == 0
    m.text_ok = bool(tr.text.strip())
    m.has_error = len(tr.errors) > 0

    if case.expected_intent:
        m.intent_ok = (tr.actual_intent is None) or (tr.actual_intent == case.expected_intent)
    else:
        m.intent_ok = None

    if case.expected_tools:
        m.tool_ok = all(
            any(hint in key for key in tr.tools_hit)
            for hint in case.expected_tools
        )
    else:
        m.tool_ok = None

    return m


async def run_inprocess(cfg: EvalConfig, cases: list[EvalCase]) -> list[EvalResult]:
    agent_ids = sorted({c.agent_id for c in cases}) or [cfg.agent_id]
    await context.prepare(agent_ids)

    sem = asyncio.Semaphore(cfg.concurrency)
    collector = Collector(cfg.run_id)

    async def one(case: EvalCase) -> EvalResult:
        async with sem:
            try:
                agent = await context.get_agent(case.agent_id)
                tr = await collector.collect(
                    agent,
                    case,
                    collector.session_id_for(case),
                    cfg.user_token,
                    timeout=cfg.timeout,
                )
            except Exception as e:  # noqa: BLE001
                tr = Transcript(errors=[str(e)])
            return EvalResult(case=case, transcript=tr, rule=compute_rule_metrics(case, tr))

    results = await asyncio.gather(*(one(c) for c in cases))
    await context.cleanup(agent_ids)
    return list(results)


async def run_http(cfg: EvalConfig, cases: list[EvalCase]) -> list[EvalResult]:
    sem = asyncio.Semaphore(cfg.concurrency)
    collector = HttpCollector(cfg.run_id, cfg.base_url)

    async def one(case: EvalCase) -> EvalResult:
        async with sem:
            tr = await collector.collect(
                case, collector.session_id_for(case), cfg.user_token, timeout=cfg.timeout
            )
            return EvalResult(case=case, transcript=tr, rule=compute_rule_metrics(case, tr))

    return list(await asyncio.gather(*(one(c) for c in cases)))


async def run(cfg: EvalConfig, cases: list[EvalCase]) -> list[EvalResult]:
    if cfg.mode == "http":
        return await run_http(cfg, cases)
    return await run_inprocess(cfg, cases)