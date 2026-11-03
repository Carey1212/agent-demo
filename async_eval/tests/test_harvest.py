"""harvest 用例切分逻辑测试（mock graph，不依赖外部服务）。"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402

from async_eval.core.harvest import _build_cases  # noqa: E402


class FakeState:
    def __init__(self, values):
        self.values = values


class FakeGraph:
    def __init__(self, messages, history):
        self._messages = messages
        self._history = history  # [(消息条数, intent), ...]

    async def aget_state(self, config):
        return FakeState({"messages": self._messages})

    async def aget_state_history(self, config, limit=10000):
        for n, intent in self._history:
            yield FakeState({"messages": self._messages[:n], "intent": intent})


class FakeAgent:
    graph = None

    def __init__(self, graph):
        self.graph = graph


def _msgs():
    return [
        HumanMessage(content="你好"),
        AIMessage(content="你好，有什么可以帮你？"),
        ToolMessage(content='{"id": 1}', name="query_course_by_id", tool_call_id="t1"),
        HumanMessage(content="《Java进阶编程实战》多少钱？"),
        AIMessage(content="该课程的价格是 1980 元。"),
    ]


def test_build_cases_with_intent_and_tools():
    graph = FakeGraph(_msgs(), [(1, "UNKNOWN"), (4, "CONSULT")])
    agent = FakeAgent(graph)

    async def run():
        return await _build_cases(
            agent,
            [{"session_id": "abc123def456", "title": "你好", "update_time": None}],
            agent_id=1001,
            min_turns=1,
            max_turns=4,
            max_cases=5,
            label_points=False,
        )

    res = asyncio.run(run())
    assert len(res.cases) == 1
    case = res.cases[0]
    assert case.expected_intent == "CONSULT"          # 末轮意图真值
    assert case.expected_tools == ["courseInfo"]      # 从 ToolMessage 推导
    assert case.question == "你好"
    assert case.turns == ["《Java进阶编程实战》多少钱？"]
    assert "prod" in case.tags
    assert "intents:UNKNOWN,CONSULT" in case.tags


def test_sliding_window_keeps_last_turns():
    graph = FakeGraph(_msgs(), [(1, "UNKNOWN"), (4, "CONSULT")])
    agent = FakeAgent(graph)

    async def run():
        return await _build_cases(
            agent,
            [{"session_id": "abc123def456", "title": "你好", "update_time": None}],
            agent_id=1001,
            min_turns=2,     # 要求至少 2 轮
            max_turns=1,     # 窗口 = max_turns+1 = 2 轮
            max_cases=5,
            label_points=False,
        )

    res = asyncio.run(run())
    assert len(res.cases) == 1
    case = res.cases[0]
    assert case.question == "你好"
    assert case.turns == ["《Java进阶编程实战》多少钱？"]
    assert case.expected_intent == "CONSULT"


def test_min_turns_filter():
    single = [HumanMessage(content="hi"), AIMessage(content="hello")]
    graph = FakeGraph(single, [(1, "UNKNOWN")])
    agent = FakeAgent(graph)

    async def run():
        return await _build_cases(
            agent,
            [{"session_id": "abc123def456", "title": "hi", "update_time": None}],
            agent_id=1001,
            min_turns=2,
            max_turns=4,
            max_cases=5,
            label_points=False,
        )

    res = asyncio.run(run())
    assert res.cases == []


def test_dedup_same_first_question():
    msgs = [HumanMessage(content="你好"), AIMessage(content="hi")]
    graph = FakeGraph(msgs, [(1, "UNKNOWN")])
    agent = FakeAgent(graph)
    sessions = [
        {"session_id": "abc1", "title": "你好", "update_time": None},
        {"session_id": "abc2", "title": "你好", "update_time": None},
    ]

    async def run():
        return await _build_cases(
            agent,
            sessions,
            agent_id=1001,
            min_turns=1,
            max_turns=4,
            max_cases=5,
            label_points=False,
        )

    res = asyncio.run(run())
    assert len(res.cases) == 1


if __name__ == "__main__":
    test_build_cases_with_intent_and_tools()
    test_sliding_window_keeps_last_turns()
    test_min_turns_filter()
    test_dedup_same_first_question()
    print("all harvest tests passed")
