# async_eval —— 异步评测 + 反思改进

对 `agent_center` 的三个智能体（1001 RouterAgent / 1002 TextAgent / 1003 TJA2AAgent）做
**输出评测 → 归因 → 改进候选 → A/B 对比 → 实验登记** 的闭环。全部代码在 `async_eval/` 内，不改动任何现有文件。

## 目录

```
async_eval/
├── cli.py             # 命令行入口：run / ab
├── eval_config.py     # EvalConfig（configs/eval.yaml 覆盖）
├── datasets/          # 用例（sample.yaml 为示例）
├── core/
│   ├── context.py     # 惰性加载 agent、绕过 httpx2 误导入、准备/清理 PG 池
│   ├── collector.py   # 进程内 SSE 采集（多轮/意图提取/清理会话）
│   ├── http_collector.py  # HTTP 模式采集（不 import agent）
│   ├── runner.py      # asyncio 信号量并发跑批 + 规则指标
│   ├── metrics.py     # 汇总统计（准确率/P50/P95/通过率）
│   ├── judge.py       # deepseek 5 维判分（复用 ai.openai.* 配置）
│   ├── reflect.py     # 低分归因 + 生成候选 prompt
│   ├── ab.py          # baseline vs candidate A/B
│   ├── harvest.py     # 生产会话回流采集用例（chat_session + checkpointer）
│   └── registry.py    # experiments.json 实验登记
└── reporters/         # 控制台摘要 + JSON 报告
```

## 快速开始

在 `agent_center/` 目录下运行：

```bash
# 进程内模式（默认），跑全部 datasets/*.yaml
python -m async_eval.cli run

# 指定数据集 / HTTP 模式（需服务已启动）
python -m async_eval.cli run --dataset sample.yaml --mode http

# 只看规则指标，不调判分/反思
python -m async_eval.cli run --no-judge --no-reflect

# 从生产会话回流采集用例（近7天，最多200条；--dry-run 只统计不落盘）
python -m async_eval.cli harvest --agent 1001 --days 7 --max-cases 200
python -m async_eval.cli harvest --agent 1001 --days 7 --dry-run
```

输出落盘到 `async_eval/reports/`：
- `run_<id>.json`：完整结果
- `reflect_<id>.json`：低分归因 + 候选 prompt
- `candidates_<id>.json`：候选 prompt（可直接喂给 `ab`）
- `experiments.json`：历史实验轨迹

## A/B 对比

```bash
# candidates_<id>.json 是 {"route": "新提示词", ...} 结构
python -m async_eval.cli ab --candidate async_eval/reports/candidates_<id>.json
```

A/B 在进程内临时覆盖 `system_prompt_config` 对应属性运行，不写 Nacos、不改文件；
确认 candidate 有提升后，再人工决定是否发布到 Nacos（配合现有 60s 热更新生效）。

## 前置条件与已知注意

1. **依赖可达**：进程内模式需要 Postgres（checkpointer）、Redis、Nacos、以及 A2A 服务（3601-3605）可达。
   因为 `agent/__init__.py` 级联导入 `tja2a_client`，会在模块加载时 `asyncio.run` 连接 A2A 服务。
2. **httpx2 误导入**：`agent/tianji/RouterAgent.py` 顶部有 `from httpx2 import _content`（不存在该包）。
   本工具在导入前自动注册假的 `httpx2` 模块绕过，见 `core/context.py::_ensure_httpx2_shim`。
   建议后续删除该行（本工具不代改）。
3. **HTTP 模式**：无法从 graph state 提取实际意图，`actual_intent` 恒为 None，意图准确率不可校验；
   且 HTTP 模式要求服务进程本身已能启动（同样受 httpx2 影响）。
4. **多轮限制**：1002 TextAgent 无 checkpointer，多轮只是文本累加，不构成上下文记忆。
5. **候选 prompt 的 format 风险**：`BaseNodeAgent` 用 `str.format(**params)` 渲染系统提示词，
   若候选提示词里出现未转义的 `{` `}`（如 JSON 示例）会抛 KeyError，生成候选时需注意保留占位符。
6. **harvest 边界**：仅支持有 graph + checkpointer 的 agent（1001/1003），1002 无记忆不支持；
   `expected_intent` 取自 checkpoint 里存的 `intent` 真值（若历史不可用则回退到最新意图）；
   `expected_tools` 从 ToolMessage 的工具名推导（query_course_by_id→courseInfo，pre_place_order→PrePlaceOrder）；
   产出 YAML 已脱敏（只保留 session_id 前缀，不含 user_id），写为 `datasets/harvested_<date>.yaml`。