"""实验登记：把每次运行的摘要追加到 reports/experiments.json，形成版本→分数轨迹。"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def _read(path: Path) -> list[Any]:
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except Exception:
            pass
    return []


def record_experiment(run_dir: Path, entry: dict[str, Any]) -> Path:
    """追加一条实验记录，返回登记文件路径。"""
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "experiments.json"
    rows = _read(path)
    entry = {"timestamp": datetime.now().isoformat(timespec="seconds"), **entry}
    rows.append(entry)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return path