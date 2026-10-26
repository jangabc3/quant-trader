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


def make_client() -> anthropic.Anthropic:
    key = vault.get_anthropic()
    # 저장된 키가 없으면 SDK 기본 순서(ANTHROPIC_API_KEY 환경변수, ant auth 프로필 등)를 쓴다
    return anthropic.Anthropic(api_key=key, max_retries=3) if key else anthropic.Anthropic(max_retries=3)


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
            max_tokens: int = 16000) -> Reply:
        output_config: dict = {"effort": self.effort}
        if schema:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        messages: list[dict] = [{"role": "user", "content": user}]
        tokens_in = tokens_out = 0
        sources: list[dict] = []
        extra = {"tools": [WEB_SEARCH]} if web else {}
        for _ in range(5):  # 웹 검색이 길어지면 pause_turn 으로 끊겨 온다 -> 이어서 요청
            try:
                resp = self.client.beta.messages.create(
                    model=self.model, max_tokens=max_tokens, system=system, messages=messages,
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
            return Reply(text, sources, data, tokens_in, tokens_out)
        raise LLMError("웹 검색이 너무 길어져 중단했습니다")
