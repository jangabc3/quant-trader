"""Claude 호출 래퍼. 거절(refusal) 시 서버 쪽 대체 모델(fallbacks="default")로 이어 받는다."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import anthropic

from .. import vault

WEB_SEARCH = {"type": "web_search_20260209", "name": "web_search", "max_uses": 5}
BETAS = ["server-side-fallback-2026-07-01"]


class LLMError(RuntimeError):
    pass


@dataclass
class Reply:
    text: str
    sources: list[dict] = field(default_factory=list)
    data: dict | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    model: str = ""
    web_searches: int = 0

    def cost_usd(self) -> float:
        return cost_usd(self.model, self.input_tokens, self.output_tokens, self.cache_read, self.cache_write) \
            + self.web_searches * WEB_SEARCH_USD


# 웹 검색은 토큰과 별도로 1,000번에 10달러 (검색 결과로 들어온 글은 입력 토큰으로 따로 센다)
WEB_SEARCH_USD = 10.0 / 1000

# 100만 토큰당 달러 (입력, 출력, 캐시 읽기, 캐시 쓰기)
PRICES = {"claude-opus-5-5": (4.0, 20.0, 0.20, 5.0), "claude-sonnet-5-5": (2.0, 10.0, 0.20, 2.5),
          "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125), "claude-fable-5-1": (10.0, 50.0, 0.25, 12.5)}


def cost_usd(model: str, inp: int, out: int, cr: int = 0, cw: int = 0) -> float:
    a, b, c, d = PRICES.get(model, PRICES["claude-opus-5-5"])
    return (inp * a + out * b + cr * c + cw * d) / 1e6


def usage_cost(model: str, usage) -> float:
    """응답 하나의 usage 로 비용 (토큰 + 웹 검색 횟수). usage 가 없으면 0."""
    if usage is None:
        return 0.0
    n = lambda k: int(getattr(usage, k, 0) or 0)
    web = int(getattr(getattr(usage, "server_tool_use", None), "web_search_requests", 0) or 0)
    return cost_usd(model, n("input_tokens"), n("output_tokens"), n("cache_read_input_tokens"),
                    n("cache_creation_input_tokens")) + web * WEB_SEARCH_USD


def make_client() -> anthropic.Anthropic:
    key = vault.get_anthropic()
    # 저장된 키가 없으면 SDK 기본 순서(ANTHROPIC_API_KEY 환경변수, ant auth 프로필 등)를 쓴다
    # 응답 하나를 최대 5분까지만 기다린다 (기본 10분 x 재시도 3번이면 회의가 수십 분 멈춘 것처럼 보이고,
    # 회의는 한 번에 하나라 다음 회의도 못 연다). 웹 검색이 있는 보고서도 보통 1~2분이면 끝난다
    kw = {"max_retries": 2, "timeout": 300.0}
    return anthropic.Anthropic(api_key=key, **kw) if key else anthropic.Anthropic(**kw)


def _sources(content) -> list[dict]:
    out, seen = [], set()
    for b in content:
        if getattr(b, "type", "") == "web_search_tool_result" and isinstance(b.content, list):
            for r in b.content:
                url = getattr(r, "url", "")
                if url and url not in seen:
                    seen.add(url)
                    out.append({"title": getattr(r, "title", "") or url, "url": url})
    return out


class LLM:
    def __init__(self, model: str = "claude-opus-5-5", effort: str = "medium", client=None):
        self.model, self.effort = model, effort
        self._client = client

    @property
    def client(self):
        if self._client is None:
            self._client = make_client()
        return self._client

    def run(self, system: str, user: str, web: bool = False, schema: dict | None = None,
            max_tokens: int = 16000, model: str | None = None, context: str | None = None) -> Reply:
        """context: 여러 참석자가 똑같이 읽는 회의 자료. 캐시 지점을 둬서 두 번째부터는 캐시 요금(입력의 1/10~1/20)만 낸다."""
        output_config: dict = {"effort": self.effort}
        if schema:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        content = [{"type": "text", "text": context, "cache_control": {"type": "ephemeral"}}] if context else []
        content.append({"type": "text", "text": user})
        sys_blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        messages: list[dict] = [{"role": "user", "content": content}]
        model = model or self.model
        tokens_in = tokens_out = cr = cw = searches = 0
        sources: list[dict] = []
        extra = {"tools": [WEB_SEARCH]} if web else {}
        for _ in range(5):  # 웹 검색이 길어지면 pause_turn 으로 끊겨 온다 -> 이어서 요청
            try:
                resp = self.client.beta.messages.create(
                    model=model, max_tokens=max_tokens, system=sys_blocks, messages=messages,
                    output_config=output_config, betas=BETAS, fallbacks="default", **extra)
            except anthropic.AuthenticationError:
                raise LLMError("Claude API 키가 올바르지 않습니다. 설정에서 키를 확인하세요")
            except anthropic.RateLimitError:
                raise LLMError("Claude API 호출 한도에 걸렸습니다. 잠시 후 다시 시도하세요")
            except anthropic.APIStatusError as e:
                raise LLMError(f"Claude API 오류 {e.status_code}: {e.message}")
            except anthropic.APIConnectionError:
                raise LLMError("Claude API 에 연결할 수 없습니다 (인터넷 연결 확인)")
            tokens_in += resp.usage.input_tokens
            tokens_out += resp.usage.output_tokens
            cr += getattr(resp.usage, "cache_read_input_tokens", 0) or 0
            cw += getattr(resp.usage, "cache_creation_input_tokens", 0) or 0
            searches += int(getattr(getattr(resp.usage, "server_tool_use", None), "web_search_requests", 0) or 0)
            sources += _sources(resp.content)
            if resp.stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": resp.content})
                continue
            if resp.stop_reason == "refusal":
                raise LLMError("모델이 이 요청에 답하지 않았습니다")
            text = "".join(b.text for b in resp.content if b.type == "text").strip()
            data = None
            if schema:
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    raise LLMError("구조화된 결과를 읽지 못했습니다")
            return Reply(text, sources, data, tokens_in, tokens_out, cr, cw, model, searches)
        raise LLMError("웹 검색이 너무 길어져 중단했습니다")
