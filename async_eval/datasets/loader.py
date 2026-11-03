"""评测数据集加载。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ..core.types import EvalCase


def _as_list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x) for x in v]
    return [str(v)]


def load_cases(*paths: Path) -> list[EvalCase]:
    """从多个 YAML 加载用例，校验必填字段。"""
    cases: list[EvalCase] = []
    seen: set[str] = set()
    for path in paths:
        if not path.exists():
            raise FileNotFoundError(f"数据集不存在：{path}")
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        items = raw.get("cases") or []
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                raise ValueError(f"{path}: 第 {i} 条用例不是 dict")
            if "question" not in item:
                raise ValueError(f"{path}: 第 {i} 条用例缺少 question")
            case_id = item.get("case_id") or f"{path.stem}_{i}"
            if case_id in seen:
                raise ValueError(f"重复 case_id：{case_id}")
            seen.add(case_id)
            cases.append(
                EvalCase(
                    case_id=str(case_id),
                    question=str(item["question"]),
                    agent_id=int(item.get("agent_id", 1001)),
                    expected_intent=item.get("expected_intent"),
                    expected_points=_as_list(item.get("expected_points")),
                    expected_tools=_as_list(item.get("expected_tools")),
                    turns=_as_list(item.get("turns")),
                    tags=_as_list(item.get("tags")),
                )
            )
    return cases


def load_from_config(cfg) -> list[EvalCase]:
    return load_cases(*cfg.dataset_paths())