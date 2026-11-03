# 异步评测与反思模块代码说明

本文说明当前 Python 文件的职责、入口和方法调用关系。主流程由 Web 请求进程采集并提交任务，独立 Worker 消费任务，两者通过 PostgreSQL 任务表连接。

## 1. 文件职责总览

| 文件 | 主要对象 | 职责 |
| --- | --- | --- |
| `collection/service.py` | `Collector`、`observe()` | 采集 Agent 输出，生成执行快照并提交任务 |
| `collection/schemas.py` | `Snapshot`、`Judgment`、`Reflection` 等 | 定义数据结构，校验输入与模型返回 |
| `collection/repository.py` | `Repository` | 数据库操作：提交、领取、保存、重试、查询 |
| `execution/worker.py` | `main()`、`process_one()` | 后台调度评测、反思及可选的提示词改进 |
| `collection/prompt_context.py` | `record_prompt()` | 在请求上下文中记录实际模板与版本 |
| `reflection/improvement.py` | `Improver.improve()` | 按反思生成并保存候选 |
| `execution/regression.py` | `run_regression()` | 隔离执行两版提示词并保存验证依据 |
| `execution/improve_cli.py` | `execute()` | 候选生成、查询、回归、审核、导出 |
| `reflection/evaluator.py` | `Evaluator`、`OpenAIModel` | 规则检查、模型评分、模型反思 |
| `execution/cli.py` | `show()` | 按执行 ID 查询已有结果 |
| `reflection/proposals.py` | 独立工具函数 | 聚合问题、生成候选 Prompt、对比回归结果 |
| `tests/test_pipeline.py` | `PipelineTests` | 使用 mock 验证核心逻辑 |
| `__init__.py` | 无业务对象 | 标识 Python 包，不负责启动业务 |

此外，`collection/sql/001_tasks.sql` 提供建表脚本，`README.md` 说明部署、使用和当前边界。

## 2. 三个入口

### 2.1 聊天请求入口

入口位于仓库的 `web/routers/ChatRouter.py`，对应 `chat()`：

```python
output = agent.execute(req.question, req.sessionId, req.userToken)
return stream(
    observe_if_enabled(
        output, req.question, req.sessionId, req.agentId, agent.is_stop
    )
)
```

调用关系：

```text
ChatRouter.chat()
  ├─ agent.execute()：得到 Agent 的异步输出流
  ├─ service.observe_if_enabled()：判断是否包装采集逻辑
  └─ ChatRouter.stream()：创建 StreamingResponse
```

`agent.execute()` 返回异步生成器。框架消费输出流时，才逐步执行生成器中的逻辑。

### 2.2 后台处理入口

在仓库根目录执行：

```bash
python -m async_eval_codex.execution.worker
```

模块底部的 `__main__` 调用 `asyncio.run(main())`，启动领取任务的循环。

### 2.3 查询入口

在仓库根目录执行：

```bash
python -m async_eval_codex.execution.cli RUN_ID
```

将 `RUN_ID` 替换为日志中的实际执行 ID。该命令只查询结果，不提交任务或触发模型调用。

## 3. collection/service.py：采集回答并提交任务

### 对象与方法

| 对象 | 输入 | 处理 | 输出 |
| --- | --- | --- | --- |
| `observe_if_enabled()` | Agent 流、问题、会话 ID 等 | 检查 `AGENT_EVAL_ENABLED` | 原始流或包装后的流 |
| `observe()` | 同上 | 创建快照、采集输出、结束后提交任务 | 持续透传原始 SSE |
| `Collector.feed()` | 一个 SSE 数据块 | 解析事件并更新快照 | 无返回值，修改快照 |
| `submit()` | `Snapshot` | 获取连接池，调用数据库提交方法 | 等待任务入库 |

### 执行顺序

```text
observe_if_enabled()
  ├─ 开关关闭 → 返回原始 Agent 流
  └─ 开关开启 → 返回 observe() 异步生成器
                    ├─ 创建 Snapshot
                    ├─ 创建 Collector(snapshot)
                    ├─ 循环读取 Agent 数据块
                    │    ├─ collector.feed(chunk)
                    │    └─ yield chunk 给前端
                    ├─ 检查 agent.is_stop()
                    └─ finally
                         ├─ 记录耗时
                         └─ submit(snapshot)
```

`Collector` 内部保存当前快照 `snapshot` 和未解析完的 SSE 缓冲区 `buffer`。遇到消息分隔符后，解析完整消息：

| 事件类型 | 处理方式 |
| --- | --- |
| `1001` | 文本追加到 `snapshot.answer` |
| `1003` | 字典形式的工具结果追加到 `snapshot.tool_results` |
| `2001` | 保存错误，将状态设置为 `execution_error` |
| `1002` | 透传，不单独触发提交 |

任务在 Agent 流迭代完成或退出时提交，而不是收到 `1002` 就立即提交。

`observe()` 在结束后使用 `asyncio.to_thread()` 调用同步的 `agent.is_stop()`，主动停止时将执行标记为 `interrupted`。随后在 `finally` 中计算耗时，并设置三秒任务提交超时。

注意两个同名方法的职责：

```text
service.submit(snapshot)
  → 获取应用连接池
  → Repository.submit(snapshot)
      → 执行 INSERT SQL
```

请求阶段不调用评测模型。数据库提交仍在请求收尾阶段等待，可能延后 HTTP 流最终关闭；提交失败仅记录日志，当前没有补偿队列。

## 4. collection/schemas.py：数据结构和校验

所有模型继承 Pydantic 的 `BaseModel`，负责组织和校验数据，不负责业务调度。

| 类 | 表示的数据 | 使用位置 |
| --- | --- | --- |
| `Snapshot` | 一次 Agent 执行快照 | `collection/service.py` 创建，`execution/worker.py` 恢复 |
| `Assessment` | 一个评分维度 | 嵌套在 `Judgment` 中 |
| `Judgment` | 完整模型评分 | `Evaluator.evaluate()` 校验 |
| `Finding` | 一条反思发现 | 嵌套在 `Reflection` 中 |
| `Reflection` | 多条反思发现 | `Evaluator.reflect()` 校验 |

```text
Snapshot
  ├─ run_id、session_id、agent_id
  ├─ question、answer、tool_results
  ├─ errors、status、duration_ms
  └─ missing_evidence

Judgment
  ├─ completion: Assessment
  ├─ consistency: Assessment 或 None
  ├─ completeness: Assessment
  ├─ expression: Assessment
  └─ issues、insufficient_evidence

Reflection
  └─ findings: list[Finding]
       ├─ category、problem、evidence
       ├─ hypothesis、confidence
       └─ suggestion、verification
```

常用方法：

```python
snapshot.model_dump()               # 转成 Python 字典
snapshot.model_dump_json()          # 转成 JSON 字符串
Snapshot.model_validate(payload)    # 从字典恢复对象并校验
```

当前评分要求分数为 1 至 5 的整数，但评分证据列表允许为空，程序没有验证证据路径实际存在。反思至少需要一条发现，每条发现至少包含一条证据。

## 5. collection/repository.py：任务和结果存储

使用 `Repository(pool)` 创建对象，传入 PostgreSQL 连接池。所有方法围绕 `agent_eval_task` 表工作。

| 方法 | 调用方 | 作用 |
| --- | --- | --- |
| `submit(snapshot)` | `service.submit()` | 新增任务，相同 `run_id` 忽略 |
| `claim()` | `worker.process_one()` | 领取任务，增加领取次数并设置租约 |
| `save_evaluation()` | `worker.process_one()` | 保存评分，任务仍处于运行中 |
| `finish()` | `worker.process_one()` | 保存反思并标记成功 |
| `fail()` | `worker.process_one()` | 保存错误，安排重试或标记失败 |
| `get(run_id)` | `cli.show()` | 查询状态、评分、反思、错误与领取次数 |

### 领取任务

`claim()` 选择待执行任务、到达重试时间的任务，或租约过期的任务。通过 `FOR UPDATE SKIP LOCKED` 跳过其他 Worker 锁定的记录。

领取后设置：

```text
status = running
attempts = attempts + 1
lease_token = 本次领取的随机令牌
lease_until = 当前时间 + 180 秒
```

返回值：

```python
(run_id, snapshot, evaluation, attempts, token)
```

`evaluation` 可能已有值，用于反思失败后的重试复用。

结果写入同时检查执行 ID、令牌、运行状态和租约有效期，防止旧 Worker 覆盖新结果。

### 任务状态

```text
pending → running → succeeded
                  → retry_wait → running
                  → failed
```

默认最多领取三次。首次失败等待两秒，第二次失败等待四秒，第三次失败终止。Worker 崩溃后，由后续领取逻辑恢复租约过期的任务；达到次数上限的过期任务标记失败。

## 6. execution/worker.py：后台调度

### main()

```text
__main__
  → asyncio.run(main())
      → 获取 PostgreSQL 连接池
      → Repository(pool)
      → OpenAIModel()
      → Evaluator(model)
      → 循环调用 process_one(repository, evaluator)
```

没有任务时等待一秒；队列访问异常时等待五秒；退出时关闭连接池。单个 Worker 串行处理，可启动多个 Worker 并行消费。

### process_one()

这是处理单条任务的核心函数：

```text
Repository.claim()
  → 没有任务：返回 False
  → 有任务：进入 140 秒处理时限
      → Snapshot.model_validate(payload)
      → evaluation 不存在时
          → Evaluator.evaluate(snapshot)
          → Repository.save_evaluation(...)
      → evaluation["reflect"] 为 True 时
          → Evaluator.reflect(snapshot, evaluation)
      → Repository.finish(...)
```

处理发生异常时调用 `Repository.fail()`。先保存评测再反思，使反思重试能够复用评分。普通任务处理时限为 140 秒、租约 180 秒；开启改进后处理时限为 540 秒、租约 600 秒。

任务的 `status=succeeded` 表示后台处理完成；`evaluation.status=fail` 表示回答不合格。两者可以同时成立。

## 7. reflection/evaluator.py：评测和反思

### 两个类的分工

- `Evaluator`：决定检查顺序、评分结果和是否需要反思。
- `OpenAIModel`：构造模型请求，发送请求，解析 JSON。

`Evaluator(model, threshold=3)` 接收模型调用对象。实际运行传入 `OpenAIModel()`，测试可传入异步 mock。

### Evaluator.evaluate(snapshot)

执行顺序：

1. 中断执行返回 `inconclusive`，不反思。
2. 检查已有错误、执行失败状态和空回答。
3. 存在规则问题时直接返回判定，跳过模型评分，设置需要反思。
4. 否则使用 `JUDGE_PROMPT` 调用模型。
5. 通过 `Judgment.model_validate()` 校验模型结果。
6. 对可用维度计算平均分。
7. 任一维度低于阈值或 `issues` 非空，判定为 `fail`。
8. 没有失败但证据不足，判定为 `inconclusive`；其余为 `pass`。

模型评分路径的返回结构：

```python
{
    "status": "fail",
    "score": 2.5,
    "judgment": {...},
    "reflect": True,
}
```

规则直接判定的结果包含 `issues`，没有 `score` 和 `judgment`。

### Evaluator.reflect(snapshot, evaluation)

```text
组合 snapshot 和 evaluation
  → 使用 REFLECT_PROMPT 调用模型
  → Reflection.model_validate() 校验
  → 返回反思字典
```

反思包含问题、证据、原因假设、可信程度、建议和验证方式，不执行修改。

### OpenAIModel.__call__(instruction, data)

```text
读取 ai.openai.* 配置
  → 创建 AsyncOpenAI 客户端
  → instruction 放入 system 消息
  → data 序列化后放入 user 消息
  → chat.completions.create()
  → json.loads() 解析结果
```

调用使用 `temperature=0`、60 秒请求超时和 JSON 返回模式，关闭 SDK 自动重试，由任务层管理重试。

因为定义了 `__call__()`，对象可以像函数一样调用：

```python
model = OpenAIModel()
result = await model(instruction, data)
```

## 8. execution/cli.py：结果查询

```text
argparse 读取 run_id
  → asyncio.run(show(run_id))
      → 获取数据库连接池
      → Repository.get(run_id)
      → 将结果打印为 JSON
      → 关闭连接池
```

查询结果包括任务状态、评测、反思、最后错误和领取次数。没有找到记录时输出 JSON `null`。

## 9. reflection/proposals.py：独立改进工具

`generate_proposal()` 已通过 `Improver.improve()` 接入 Worker；其他函数也可独立使用。

| 函数 | 作用 |
| --- | --- |
| `aggregate(reflections)` | 按问题类别汇总执行 ID 和去重建议 |
| `generate_proposal(model, original, findings, case_ids)` | 调用模型生成候选 Prompt，再校验 |
| `make_proposal(original, candidate, case_ids)` | 校验候选非空及占位符，生成差异 |
| `placeholders(prompt)` | 提取占位符、格式说明和转换标记 |
| `compare(baseline, candidate)` | 对比相同用例集合的评测状态 |

生成候选的调用关系：

```text
generate_proposal()
  → model(...) 生成 candidate
  → make_proposal()
      → placeholders(original)
      → placeholders(candidate)
      → 比较占位符
      → 返回原文哈希、候选文本、差异、关联用例和待审核状态
```

`compare()` 接收已经产生的两组结果，输出修复、退化、不确定项和是否建议审核。它不会执行 Agent 或重跑业务请求。`Improver` 将候选保存到 `agent_eval_proposal`；回归 CLI 保存对比报告。

## 10. tests/test_pipeline.py：离线测试

`PipelineTests` 继承 `unittest.IsolatedAsyncioTestCase`，覆盖：

- SSE 透传与回答拼接。
- 提交失败不破坏输出。
- 规则失败时跳过模型评分。
- 中断不按完整回答评测。
- 非法分数被拒绝。
- 反思重试复用已有评分。
- Prompt 占位符保护。
- 回归退化识别。

运行：

```bash
python -m unittest discover -s async_eval_codex/tests -v
```

测试使用 mock，不验证真实 PostgreSQL 并发或真实模型调用。

## 11. 完整调用关系

```text
Web 请求进程
ChatRouter.chat()
  → service.observe_if_enabled()
  → service.observe()
      ├─ schemas.Snapshot
      ├─ service.Collector.feed()
      └─ service.submit()
          → repository.Repository.submit()
                         │
                         ▼
                 PostgreSQL 任务表
                         │
                         ▼
独立 Worker 进程
worker.main()
  → worker.process_one()
      ├─ Repository.claim()
      ├─ schemas.Snapshot.model_validate()
      ├─ evaluator.Evaluator.evaluate()
      │    ├─ evaluator.OpenAIModel.__call__()
      │    └─ schemas.Judgment.model_validate()
      ├─ Repository.save_evaluation()
      ├─ evaluator.Evaluator.reflect()
      │    ├─ evaluator.OpenAIModel.__call__()
      │    └─ schemas.Reflection.model_validate()
      └─ Repository.finish() / fail()

查询进程
cli.show()
  → Repository.get()

独立改进工具
proposals.aggregate()
proposals.generate_proposal()
  → make_proposal()
      → placeholders()
proposals.compare()
```

## 12. 当前边界与阅读顺序

- 默认关闭采集，需要设置 `AGENT_EVAL_ENABLED=true`。
- 数据库迁移需手动执行，Worker 需独立启动。
- 自动采集只接入 `/chat`，直接调用 Agent 需要自行包装。
- 当前采集 SSE 回答、工具结果、异常以及执行时实际使用的 Prompt 原文与版本；没有完整历史、工具参数和完整节点轨迹。
- 任务入库前发生进程退出或提交失败，可能丢失评测任务。
- 设置 `AGENT_IMPROVEMENT_ENABLED=true` 后自动生成并保存候选；回归、审核和导出通过 CLI 执行，不自动替换线上 Prompt。

推荐阅读顺序：`collection/service.py → execution/worker.py → reflection/evaluator.py → collection/repository.py → collection/schemas.py`，然后阅读 `execution/cli.py`、`reflection/proposals.py` 和测试。

## 13. 反思后的提示词改进调用关系

```text
worker.process_one()
  → 保存反思 Repository.save_reflection()
  → Improver.improve(snapshot, reflection, lease_token)
      → Reflection.model_validate()
      → 按 action、target 和 confidence 分组
      → 从 snapshot.prompts 选取唯一原版本
      → Repository.get_proposal() 检查是否已有候选
      → generate_proposal() 调用模型
          → make_proposal() 校验版本和模板、生成差异
      → Repository.save_proposal() 保存候选
  → Repository.finish()
```

缺少目标、缺少版本或可信度低时，仅保存待调查建议。候选生成使用执行快照中的原文，
不会重新导入 Agent 读取热更新后的配置。开关、迁移和 CLI 示例详见 README.md 的改进章节。
