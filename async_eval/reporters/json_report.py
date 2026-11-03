"""JSON 报告落盘。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..core.types import to_dict


def write_run_report(run_dir: Path, run_id: str, payload: dict[str, Any]) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / f"run_{run_id}.json"
    path.write_text(json.dumps(to_dict(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(to_dict(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    return path