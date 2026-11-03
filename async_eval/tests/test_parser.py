"""基础单元测试（不依赖外部服务）。"""
import sys
from pathlib import Path

# 使 agent_center 成为可导入根
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from async_eval.core.collector import parse_sse_event, sanitize_id  # noqa: E402


def test_parse_sse_event_text():
    raw = 'data: {"eventType": 1001, "eventData": "你好"}\n\n'
    etype, data = parse_sse_event(raw)
    assert etype == 1001
    assert data == "你好"


def test_parse_sse_stop():
    raw = 'data: {"eventType": 1002}\n\n'
    etype, data = parse_sse_event(raw)
    assert etype == 1002
    assert data is None


def test_parse_sse_garbage():
    etype, data = parse_sse_event("not a valid data line")
    assert etype is None
    assert data is None


def test_sanitize_id():
    assert sanitize_id("intent_recommend_01") == "intent_recommend_01"
    assert sanitize_id("a b/c?") == "a_b_c_"


if __name__ == "__main__":
    test_parse_sse_event_text()
    test_parse_sse_stop()
    test_parse_sse_garbage()
    test_sanitize_id()
    print("all tests passed")