"""큐비: 모든 도구가 키 없이(체험 데이터로) 돌아가는지, 대화 고리(도구 호출 -> 결과 -> 답), 키가 없을 때 안내."""
from types import SimpleNamespace as NS

import pytest

from qtrader import config
from qtrader.agents.assistant import TOOLS, Assistant
from qtrader.agents.llm import LLMError

ARGS = {"symbol": "005930", "symbols": ["005930", "AAPL"], "market": "KR", "kind": "amount", "limit": 5}


@pytest.fixture
def bot(isolated_home):
    from qtrader.data import write_demo_csv
    from qtrader.engine import build_context
    write_demo_csv(isolated_home / "data", config.load())
    return Assistant(build_context, lambda: None, config.load)


def test_every_tool_runs_offline(bot):
    names = [t["name"] for t in TOOLS]
    assert len(names) == len(set(names)) >= 20
    for t in TOOLS:
        if t["name"] == "start_meeting":  # 실제 회의(비용)는 열지 않는다
            continue
        args = {k: ARGS[k] for k in t["input_schema"]["required"]}
        out = bot.call(t["name"], args)
        assert isinstance(out, str) and out, t["name"]


def test_tool_schemas_are_strict():
    for t in TOOLS:
        s = t["input_schema"]
        assert t["strict"] and s["additionalProperties"] is False and set(s["required"]) <= set(s["properties"])


class FakeClient:
    def __init__(self, replies):
        self.replies, self.sent = list(replies), []
        self.beta = NS(messages=NS(create=self.create))

    def create(self, **kw):
        self.sent.append(kw)
        return self.replies.pop(0)


def test_chat_loop_runs_tool_then_answers(bot):
    use = NS(type="tool_use", id="t1", name="my_rules", input={})
    bot._client = FakeClient([NS(content=[use], stop_reason="tool_use"),
                              NS(content=[NS(type="text", text="**결론** 기준을 지키는 중이에요")], stop_reason="end_turn")])
    r = bot.chat(None, "내 기준이 뭐야?")
    assert r["answer"].startswith("**결론**") and r["tools"] == ["my_rules"]
    second = bot._client.sent[1]
    assert second["messages"][2]["content"][0]["type"] == "tool_result"  # 대화 목록은 같은 객체라 순서로 확인
    assert second["output_config"]["effort"] == "medium" and second["cache_control"] == {"type": "ephemeral"}


def test_chat_without_key_explains(bot):
    def boom(**kw):
        raise TypeError('"Could not resolve authentication method. Expected one of api_key"')
    bot._client = NS(beta=NS(messages=NS(create=boom)))
    with pytest.raises(LLMError, match="Claude API 키"):
        bot.chat("x", "안녕")
    assert bot.threads["x"] == []  # 실패한 질문은 대화에 남기지 않는다
