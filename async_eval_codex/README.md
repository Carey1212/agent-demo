# 在线异步评测、反思与提示词改进

Agent 输出结束后，`ChatRouter` 的透传包装器采集 SSE，并将执行快照保存到 PostgreSQL。
独立 Worker 执行规则检查、模型评分及失败反思，开启改进后生成并保存候选提示词。模块不依赖其他评测目录。

## 目录分类

```text
async_eval_codex/
├── collection/              # 收集：采集、数据模型和存储
│   ├── service.py
│   ├── prompt_context.py
│   ├── schemas.py
│   ├── repository.py
│   └── sql/
├── reflection/              # 反思：评测、归因和提示词改进
│   ├── evaluator.py
│   ├── improvement.py
│   └── proposals.py
├── execution/               # 执行：后台任务、回归和命令行
│   ├── worker.py
│   ├── regression.py
│   ├── cli.py
│   └── improve_cli.py
├── tests/
├── CODE_GUIDE.md
└── README.md
```

原根目录下的 Python 模块已迁移，外部调用需使用新的包路径；以下命令均已更新。

## 启用

1. 审核并手动执行 `collection/sql/001_tasks.sql`，目标是现有 checkpointer PostgreSQL 数据库。
2. 在 Web 服务环境设置 `AGENT_EVAL_ENABLED=true`，重启服务。
3. 在项目根目录启动 `python -m async_eval_codex.execution.worker`。
4. 从日志取得 `run_id`，使用 `python -m async_eval_codex.execution.cli RUN_ID` 查询结果。

默认关闭采集。迁移不会自动执行。模型调用复用 `ai.openai.*` 配置，启用 Worker
会将任务快照发送到该模型服务；采集器不保存请求中的 `userToken`，但业务文本和
工具结果本身仍可能含敏感信息，上线前应按业务需要配置数据保留与脱敏策略。

## 处理语义

- 模型评测不在请求线程执行；输出结束后的数据库提交最长等待三秒。
- `run_id` 唯一约束防止相同任务重复入库；评测结果保存后，反思重试复用已有评分。
- Worker 使用 `FOR UPDATE SKIP LOCKED` 和随机令牌；普通任务租约 180 秒、处理上限 140 秒；开启改进后分别为 600 秒、540 秒。
- 每个 Worker 串行执行；可启动多个 Worker 控制并行度。最多领取三次，超时租约可恢复。
- 模型格式或执行错误记录为任务失败，与 Agent 的业务评测状态分离。
- 评分低于 3 或发现问题触发反思；空输出、Agent 异常优先由规则判定。
- 中断执行标为证据不足，不评判完整性。无法获取事实依据时一致性评分可以为空。
- 任务提交失败仅记录日志，不影响已发送回答；进程在入库前崩溃以及客户端断开时
  的取消可能导致任务丢失。本实现不提供请求与任务的跨系统原子提交保证。

## 当前证据边界

SSE 提供回答、返回给前端的工具结果及错误事件。节点执行时额外记录实际使用的 Prompt 原文、SHA-256 和渲染方式。尚不能提供完整历史、工具参数及内部节点轨迹，快照明确标记这些缺失。正常迭代结束视为完成，
停止标记用于识别主动停止。仅覆盖 `/chat` 入口，直接调用 `agent.execute()` 需要
自行使用 `observe` 包装。没有新增公开查询接口，避免暴露会话内容。

## 反思后自动改进提示词

1. 手动执行 `collection/sql/002_proposals.sql`，新建候选表；已有 `001_tasks.sql` 无需重复迁移。
2. Web 服务继续设置 `AGENT_EVAL_ENABLED=true`，并重启以加载 Prompt 采集代码。
3. 在 Worker 进程设置 `AGENT_IMPROVEMENT_ENABLED=true` 后启动：

```bash
AGENT_IMPROVEMENT_ENABLED=true python -m async_eval_codex.execution.worker
```

这只配置 Worker 的改进开关。Web 和 Worker 分属不同进程，各自设置对应环境变量。

自动处理顺序：

```text
快照 → 评测 → 保存评分 → 反思 → 保存反思
     → 按 target 分组 → 生成候选 → 校验模板 → 保存候选 → 完成任务
```

- 只有 `action=prompt`、可信度非 low、目标存在于快照且版本唯一时，才调用候选生成模型。
- 生成输入包括原模板、反思、用户问题、实际回答和工具结果，要求针对通用问题做精准修改，不硬编码本次业务值。
- TextAgent 原文不渲染；节点模板和 A2A 路由使用 `str.format`。候选按对应模式校验占位符。
- 候选保存原文、原版本、候选版本、差异、关联执行和反思证据；状态为 `pending_review`。
- 非提示词问题、低可信问题和缺失版本的旧数据保存为 `needs_investigation`，不猜测修改目标。
- 失败重试复用已保存的评分、反思及候选。若改进阶段最终失败，仍可查询先前保存的结果。
- 开关默认关闭；不会自动修改 Agent、Nacos 或线上 Prompt。

### 手动补生成、查询

把示例中的 `RUN_ID`、`PROPOSAL_ID` 替换成实际 ID：

```bash
python -m async_eval_codex.execution.improve_cli generate RUN_ID
python -m async_eval_codex.execution.improve_cli list RUN_ID
python -m async_eval_codex.execution.improve_cli show PROPOSAL_ID
```

`generate` 接受已经结束且保存了反思的任务，包括改进阶段失败的任务；仍在处理的任务不可手动补生成。
旧反思未填写 `action` 和 `target` 时默认需要调查，不会自动重新归因或从当前配置猜测原 Prompt。

### 回归、审核与导出

内置回放仅支持 `chat_text_message`，复用 TextAgent 的无工具调用方式。
其他目标需通过 `regression.run_regression()` 注入隔离执行器，执行器声明 `target` 和 `name`，
并实现 `await replay(prompt, case)`，返回执行后的 `Snapshot`。不得直接重放生产副作用。

数据集为 JSON 数组，每项包含 `case_id`、`split` 和 `snapshot`。必须包含三类用例：
`discovery`（复现）、`retention`（原有能力）、`holdout`（独立验证）。例如：

```json
[
  {"case_id":"d1","split":"discovery","snapshot":{"run_id":"r1","session_id":"test-d1","agent_id":1002,"question":"请用三句话解释 Python 列表。"}},
  {"case_id":"r1","split":"retention","snapshot":{"run_id":"r2","session_id":"test-r1","agent_id":1002,"question":"请解释 Python 字典。"}},
  {"case_id":"h1","split":"holdout","snapshot":{"run_id":"r3","session_id":"test-h1","agent_id":1002,"question":"请用两句话解释 Python 集合。"}}
]
```

这些仅展示文件结构，应按真实问题准备有代表性的案例。独立验证不得使用生成候选的原案例；
程序检查 ID 和问题原文，语义独立性仍需审核。

```bash
python -m async_eval_codex.execution.improve_cli regress PROPOSAL_ID --dataset cases.json
python -m async_eval_codex.execution.improve_cli review PROPOSAL_ID --decision approved --current-prompt current.txt --reviewer carey --note '回归通过'
python -m async_eval_codex.execution.improve_cli export PROPOSAL_ID --output candidate.txt
```

`regress` 实际调用模型并保存两版输出、评分、耗时、数据集哈希和对比结果。
只有出现复现用例修复、无退化和不确定项、原有能力保持且独立验证通过时，才建议审核。
审批检查 `current.txt` 与原版本一致、回归与候选绑定；该文件需从目标配置准确导出，程序不自动读取线上配置。
导出仅接受批准的候选，且不覆盖已有文件。发布前需再次核对线上版本，原文保留供回滚。

## 改进代码入口

| 文件 | 职责 |
| --- | --- |
| `collection/prompt_context.py` | 请求内隔离采集实际 Prompt |
| `reflection/improvement.py` | 反思分组、候选生成与审核条件检查 |
| `reflection/proposals.py` | 模型生成、模板校验、差异和对比 |
| `collection/repository.py` | 候选、回归报告及审核记录持久化 |
| `execution/regression.py` | 隔离回放与两版评测 |
| `execution/improve_cli.py` | 手动生成、查询、回归、审核、导出 |

## 验证

```bash
python -m unittest discover -s async_eval_codex/tests -v
```

测试使用内存 mock，不导入应用配置，不访问数据库或模型服务。
数据库迁移、并发领取与租约恢复的真实 PostgreSQL 联调仍需在测试环境执行。
