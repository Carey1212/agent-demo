"""评测配置：默认可覆盖，YAML 值为空/缺省时回退到内置默认。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# 项目根目录（agent_center 的父级为 agent_center，async_eval 的父级即 agent_center）
ASYNC_EVAL_DIR = Path(__file__).resolve().parent


@dataclass
class EvalConfig:
    """一次评测运行的配置。"""

    # ---- 数据与目标 ----
    datasets: list[str] = field(default_factory=list)          # datasets/*.yaml 文件名列表，空 = 全部
    agent_id: int = 1001                                       # 默认被测智能体（用例未指定时）
    user_token: str = ""                                       # 进程内模式下传给 agent 的 userToken

    # ---- 运行模式 ----
    mode: str = "inprocess"                                    # inprocess | http
    concurrency: int = 5                                       # 并发度
    timeout: float = 120.0                                     # 单用例超时（秒）
    max_turns: int = 4                                         # 多轮最大轮数
    base_url: str = "http://127.0.0.1:18089"                  # HTTP 模式目标服务
    run_id: str = ""                                           # 留空自动生成
    run_dir: str = "async_eval/reports"                        # 报告输出目录（相对 cwd）

    # ---- 判分 ----
    judge: bool = True                                         # 是否启用 LLM 判分
    judge_provider: str = "openai"                             # 复用 ai.openai 配置
    judge_model: str | None = None                             # None -> 使用 ai.openai.model
    judge_temperature: float = 0.0
    pass_threshold: float = 3.5                                # overall < 阈值 视为低分用例
    judge_concurrency: int = 3                                 # 判分并发

    @classmethod
    def from_yaml(cls, path: str | Path) -> "EvalConfig":
        """从 YAML 加载，缺省键使用默认值。"""
        path = Path(path)
        raw: dict[str, Any] = {}
        if path.exists():
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

        fields = cls.__dataclass_fields__  # type: ignore[attr-defined]
        kwargs: dict[str, Any] = {}
        for key, f in fields.items():
            val = raw.get(key)
            if val is None:
                kwargs[key] = f.default
            else:
                kwargs[key] = val
        return cls(**kwargs)

    @classmethod
    def defaults(cls, **overrides: Any) -> "EvalConfig":
        cfg = cls()
        for k, v in overrides.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
        return cfg

    def resolve_run_dir(self) -> Path:
        return Path(self.run_dir).expanduser().resolve()

    def dataset_paths(self) -> list[Path]:
        ds_dir = ASYNC_EVAL_DIR / "datasets"
        if self.datasets:
            return [ds_dir / name for name in self.datasets]
        return sorted(p for p in ds_dir.glob("*.yaml") if not p.name.startswith("_"))


DEFAULT_CONFIG_PATH = ASYNC_EVAL_DIR / "configs" / "eval.yaml"