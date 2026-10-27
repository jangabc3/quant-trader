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


class FakeStream:
    def __init__(self, reply):
        self.reply = reply

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        for b in self.reply.content:
            if b.type == "text":
                for i in range(0, len(b.text), 4):
                    yield NS(type="text", text=b.text[i:i + 4])

    def get_final_message(self):
        return self.reply


class FakeClient:
    def __init__(self, replies):
        self.replies, self.sent = list(replies), []
        self.beta = NS(messages=NS(stream=self.stream))

    def stream(self, **kw):
        self.sent.append(kw)
        return FakeStream(self.replies.pop(0))


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
    bot._client = NS(beta=NS(messages=NS(stream=boom)))
    with pytest.raises(LLMError, match="Claude API 키"):
        bot.chat("x", "안녕")
    assert bot.threads["x"] == []  # 실패한 질문은 대화에 남기지 않는다


def test_stream_sends_text_as_it_comes(bot):
    use = [NS(type="tool_use", id="a", name="my_rules", input={}), NS(type="tool_use", id="b", name="app_help", input={})]
    bot._client = FakeClient([NS(content=use, stop_reason="tool_use"),
                              NS(content=[NS(type="text", text="**결론** 두 도구를 동시에 봤어요")], stop_reason="end_turn")])
    evs = list(bot.chat_stream(None, "알려줘"))
    kinds = [e["type"] for e in evs]
    assert kinds[0] == "start" and kinds[-1] == "done" and kinds.count("tool") == 2 and kinds.count("text") > 2
    assert evs[-1]["answer"].startswith("**결론**")
    results = bot._client.sent[1]["messages"][2]["content"]
    assert [r["tool_use_id"] for r in results] == ["a", "b"]  # 두 결과를 한 메시지로
