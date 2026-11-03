# Repository Guidelines

## 项目结构与模块组织

本项目是基于 FastAPI、LangChain 和 LangGraph 的 Python 智能体服务。`main.py` 启动 Uvicorn；`web/routers/` 提供认证、会话和聊天接口；`agent/` 保存智能体、节点、工具和系统提示词。`config/` 管理配置与服务连接，`dao/` 和 `dao/pojo/` 负责数据访问与实体，`vo/` 保存接口对象，`common/`、`util/` 提供公共能力。数据库脚本位于 `sql/`。`async_eval/` 包含评测 CLI、数据集、报告生成器和测试；具体流程见 `async_eval/README.md`。

## 环境、运行与评测命令

在仓库根目录执行：

- `conda env create -f environment.yml`：按现有版本创建 Python 3.13.5 环境。
- `conda activate ac_env_3135`：激活开发环境。
- `python main.py`：按配置启动 Web 服务；需先准备数据库、Redis、Nacos 和相关 A2A 服务。
- `python -m async_eval.cli run --no-judge --no-reflect`：执行规则评测，仍需智能体依赖服务可用。
- `python -m async_eval.cli run --dataset sample.yaml --mode http`：通过已启动的服务执行示例评测。

项目没有独立构建步骤。遵循 `environment.yml` 的依赖版本，不擅自升级大版本；国内网络环境优先使用合适的国内镜像。

## 代码风格与命名

使用四空格缩进。类名采用 `PascalCase`，函数和变量采用 `snake_case`，常量采用 `UPPER_SNAKE_CASE`。既有服务模块常用 `BaseAgent.py` 形式，评测模块使用 `http_collector.py` 形式；新增文件遵循所在目录惯例。代码、注释和新提交信息使用英文。未发现统一 formatter 或 linter 配置，避免无关的批量格式化。

## 测试要求

测试位于 `async_eval/tests/`，采用 `test_*.py` 文件及 `test_*` 函数，使用普通断言与 mock graph 隔离外部服务。可运行 `python async_eval/tests/test_parser.py`；若环境已安装 pytest，可执行 `python -m pytest async_eval/tests/`。pytest 未列入现有环境依赖，仓库也未设定覆盖率门槛。修改解析、采集或用例切分逻辑时补充对应测试；提交前运行相关检查，并如实记录失败原因。

## 提交与 Pull Request

近期提交多为“代码优化”，尚无明确的结构化规范。新提交使用简短、具体的英文祈使句，例如 `Fix SSE stop event parsing`。PR 应说明问题、行为变化、测试命令与结果，关联相关 issue；配置或接口变化需附迁移说明和请求示例。

## 配置安全与 Agent 约束

不要提交凭证、生产会话、日志或生成报告中的敏感信息。调整连接前检查 `application.yml` 和配置加载逻辑。禁止修改任何 `settings.json`；遇到配置中明确的 deny 操作立即停止并告知 carey。删除、覆盖已有内容、推送、发布或向外部服务发送内容前先取得确认。与用户沟通使用简体中文，并称呼用户为 carey。
