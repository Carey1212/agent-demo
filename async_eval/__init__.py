"""async_eval  —— 天机 AI 智能体中心「异步评测 + 反思改进」工具集。

评测闭环：
    datasets ──> runner(并发跑批采集SSE) ──> metrics(规则) + judge(LLM 打分)
                       │
        报告(reporters) ◄── reflect(归因/候选prompt) ──> ab(A/B 对比) ──> registry(实验登记)

仅新增在本目录，不修改 agent_center 任何现有代码。
"""

__version__ = "0.1.0"