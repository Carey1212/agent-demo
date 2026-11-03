---
name: git-commit-standards
description: Use when the user asks to commit code, prepare a Git commit message, or push commits; 用户要求提交代码、生成提交信息或推送 Git 提交时使用。
---

# Git 提交规范

让每次提交范围清楚、信息具体、检查结果可追溯。与用户用简体中文沟通并称呼 carey；提交信息使用英文。遵循当前用户要求及所在目录的 AGENTS.md，冲突时以用户明确要求为准。

## 1. 明确操作范围

| 用户要求 | 执行范围 |
| --- | --- |
| “生成提交信息” | 查看差异并生成文案，不创建 commit |
| “提交代码” | 检查、暂存并创建本地 commit，不自动 push |
| “推送代码”或“上传项目” | 检查目标仓库、分支和全部待推送提交后 push；存在未提交变更时，按请求范围决定是否需先提交 |
| “创建或修改 skill” | 编写和验证文件，不因此自动 commit 或 push |

已有明确授权不反复索要确认。只有提交范围无法判断、需要影响他人改动或涉及未授权的破坏性操作时，集中说明问题并确认。

## 2. 提交前检查

先执行只读检查，按依赖顺序查看结果：

```bash
git status --short
git branch --show-current
git diff --stat
git diff --cached --stat
git diff
git diff --cached
```

- 核对新增文件内容；普通 `git diff` 不包含未跟踪文件。
- 暂存区和工作区分别检查，暂存区里的文件不一定属于本次任务。
- 根据实际差异确定范围，不凭文件名或此前聊天生成提交结论。
- 暂存区存在来源不明或不相关修改时，保留其状态，先确认范围。不要自动 reset、stash 或连带提交。
- 一个提交对应一个明确目的。多个独立目的分开提交，同一文件混有不同目的时逐块暂存。
- 不为整理提交而执行无关格式化、依赖升级、切换分支或重建仓库。

## 3. 配置与敏感信息

- 不修改任何 `settings.json`。遇到明确 deny 的操作立即停止并告知用户，不换工具或命令绕过。
- 不提交 API Key、JWT 私钥、数据库密码、真实 app_secret、生产会话、日志或敏感报告。示例、注释、独立演示入口和 SQL 种子数据也需要检查。
- 不提交 `__pycache__/`、`*.pyc`、虚拟环境、IDE 缓存和本地 `.env`。已跟踪文件不受 `.gitignore` 保护，仍需检查。
- 日志中只说明可疑文件及行号，不回显秘密值。字符串匹配只是线索，必须核实是否为配置键名、占位符或真实凭证。
- 发现真实凭证时停止纳入提交，先准备环境变量占位符、无秘密的 `.env.example` 或配置样例；保留用户本地配置，必要的覆盖修改先确认。
- 推送前检查整个待推送提交范围，包括已有历史。仅从最新版本删掉秘密，不能清除旧提交里的秘密。
- 敏感历史的改写、丢弃历史后上传快照，都是独立操作，先说明影响并取得授权；不擅自创建临时仓库替代当前仓库上传。

## 4. 提交信息

默认格式：

```text
type(scope): English imperative summary
```

scope 为可选的稳定模块名，例如 `eval`、`agent`、`auth`、`skills`。标题建议不超过 72 个字符，正文按需要补充，不为凑格式添加内容。

| type | 用途 |
| --- | --- |
| feat | 新增能力 |
| fix | 修复错误 |
| refactor | 不改变预期行为的结构调整 |
| docs | 文档或纯指令型 skill |
| test | 仅新增或修改测试 |
| chore | 工程配置或维护事项 |
| perf | 性能优化 |
| build / ci | 构建依赖或持续集成 |

示例：

```text
feat(eval): Generate prompt candidates from reflections
fix(eval): Preserve prompt placeholders in candidates
refactor(eval): Group modules by responsibility
docs(skills): Add Git commit standards
```

标题描述结果，避免 `update`、`代码优化`、`修改文件` 等无法说明变化的标题。正文仅补充关键原因、行为变化、迁移要求和实际验证结果。破坏兼容性时使用 `!`，并在正文写明 `BREAKING CHANGE:` 及迁移方式。

### 调用标题检查脚本

生成提交标题后、执行 `git commit` 前，调用本技能的 `scripts/check_commit.py`。从项目根目录运行：

```bash
python skills/git-commit-standards/scripts/check_commit.py "fix(eval): Preserve prompt placeholders"
```

成功输出 `检查通过：提交标题格式正确。`，退出码为 `0`；格式错误时输出中文原因，退出码为 `1`，应修改标题后重新检查。

脚本支持省略 scope 和使用 `!`，检查类型、格式及可打印 ASCII 摘要；超过 72 字符仅提示。它不判断英文语法或标题是否准确描述差异，仍需人工或模型复核，也不会执行提交。

如果从其他目录调用，先根据当前 `SKILL.md` 的位置定位同级 `scripts/check_commit.py`，使用 Python 加脚本绝对路径执行，不依赖当前工作目录。

## 5. 验证与提交

1. 按变更运行相关检查。纯文档或 skill 优先检查结构、引用和示例，不启动业务服务。
2. 本项目评测模块可运行 `python -m unittest discover -s async_eval_codex/tests -v`；解析相关改动可运行 `python async_eval/tests/test_parser.py`。按实际修改选择，不每次全跑。
3. 测试失败时先解释失败含义，修复属于本次范围的问题。适用且必要的检查因依赖或外部服务受阻时如实报告；仍需提交时先取得明确授权。纯文档已完成对应检查时，不因未运行无关业务测试重复确认。
4. 使用明确路径或逐块暂存，避免未经检查执行 `git add .`、`git add -A`、`git commit -a`。
5. 暂存后执行 `git diff --cached --check` 并复查暂存差异。命令失败后先处理，不继续自动 commit。
6. 确认范围与提交信息一致，再执行普通 `git commit`。多行正文通过临时消息文件和 `git commit -F` 传入。
7. 空暂存区不创建空提交；Hook 失败不使用 `--no-verify` 绕过。不自动 amend，不改写已发布提交。
8. 提交后执行 `git log -1 --oneline` 和 `git status --short`，核对提交 ID 与剩余改动。

## 6. 推送

- 仅在用户授权推送时执行；检查 remote 地址、目标分支、上游关系和待推送提交范围。
- 使用明确 remote 和 refspec，不因配置缺失误推到其他仓库或分支。
- 不自动使用 force，不自动合并无关历史。非快进被拒绝时先读取远程状态再决定处理方式。
- 鉴权失败时使用已有授权连接或说明缺少登录，不请求用户在聊天中粘贴 Token。
- 自动审批拒绝时报告动作及原因；先移除实际风险并重新验证，不通过换通道绕过拒绝。
- 推送后读取远程分支 SHA，与预期提交核对后再报告成功。

## 7. 完成反馈

简短说明提交 ID、提交标题、实际验证结果、是否推送及剩余未提交内容；未运行的检查明确标注。不要把旧测试结果描述成本次验证，也不要把本地 commit 描述成已上传。

常见误区：用户催促不代表可以跳过凭证检查；“示例密钥”不自动等于可公开；仓库里原先有秘密不代表可以继续推送；创建本技能不代表获得提交或发布它的授权。
