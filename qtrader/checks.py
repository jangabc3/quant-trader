"""키 연결 테스트: 저장한 키가 실제로 되는지 단계별로 시험한다. 주문은 내지 않는다."""
from __future__ import annotations

import requests

from . import vault


def _step(name: str, fn) -> dict:
    try:
        detail = fn()
        return {"name": name, "ok": True, "detail": detail or "성공"}
    except Exception as e:  # 단계별 실패 이유를 그대로 보여 준다
        return {"name": name, "ok": False, "detail": str(e)[:300]}


def toss(cfg: dict) -> list[dict]:
    from .engine import toss_client
    from .models import Market
    t = toss_client(cfg)
    if t is None:
        return [{"name": "키 저장", "ok": False, "detail": "토스증권 Client ID/Secret 이 저장돼 있지 않아요"}]
    out = [_step("로그인 (접근 토큰 받기)", lambda: (t.token(force=True), "토큰을 받았어요")[1])]
    if not out[0]["ok"]:
        return out
    out.append(_step("시세 조회 (삼성전자)", lambda: f"현재가 {t.price(Market.KR, '005930'):,.0f}원"))
    out.append(_step("계좌 조회", lambda: f"계좌 {len(t.accounts())}개 확인 · 현금 {t.account(Market.KR).cash:,.0f}원"))

    def power():
        try:
            return f"주문 가능 금액 {t.buying_power('KRW'):,.0f}원 (주문 권한 있음)"
        except Exception as e:
            msg = str(e)
            if "403" in msg or "IP" in msg.upper() or "forbidden" in msg.lower():
                raise RuntimeError("주문 정보 조회가 막혔어요. 토스증권 WTS → Open API → 허용 IP 에 이 PC 공인 IP 를 등록했는지 확인하세요") from e
            raise
    out.append(_step("주문 권한 (허용 IP)", power))
    return out


def anthropic(cfg: dict) -> list[dict]:
    key = vault.get_anthropic()
    if not key:
        return [{"name": "키 저장", "ok": False, "detail": "Claude API 키가 저장돼 있지 않아요"}]

    def ping():
        from .agents.llm import LLM
        r = LLM(cfg["ai"]["quick_model"], "low").run("짧게 답한다.", "'연결됨' 이라고만 답해.", max_tokens=50)
        return f"{cfg['ai']['quick_model']} 응답: {r.text.strip()[:20]} (비용 약 {r.cost_usd() * 1400:.1f}원)"
    return [_step("Claude 응답 받기", ping)]


def dart(cfg: dict) -> list[dict]:
    key = vault.get("dart_api_key")
    if not key:
        return [{"name": "키 저장", "ok": False, "detail": "DART 인증키가 저장돼 있지 않아요 (선택)"}]

    def call():
        r = requests.get("https://opendart.fss.or.kr/api/company.json", params={"crtfc_key": key, "corp_code": "00126380"}, timeout=10)
        d = r.json()
        if d.get("status") != "000":
            raise RuntimeError(f"DART 응답: {d.get('message')}")
        return f"{d.get('corp_name')} 회사 정보를 받았어요"
    return [_step("DART 조회 (삼성전자)", call)]


def telegram(cfg: dict) -> list[dict]:
    tok = vault.get("telegram_bot_token")
    if not tok:
        return [{"name": "키 저장", "ok": False, "detail": "텔레그램 봇 토큰이 저장돼 있지 않아요 (선택)"}]
    out = [_step("봇 확인", lambda: "@" + requests.get(f"https://api.telegram.org/bot{tok}/getMe", timeout=8).json()["result"]["username"])]
    chat = (cfg.get("notify") or {}).get("telegram_chat_id")
    def send():
        if not chat:
            raise RuntimeError("설정 '5. 알림·이 PC'에서 '대화방 번호 자동으로 찾기'를 눌러 주세요")
        r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage", timeout=8,
                          json={"chat_id": chat, "text": "[QTrader] 연결 테스트"}).json()
        if not r.get("ok"):
            raise RuntimeError("보내지 못했어요. 봇에게 먼저 말을 걸었는지, 대화방 번호가 맞는지 확인하세요")
        return "텔레그램으로 테스트 메시지를 보냈어요"
    if out[0]["ok"]:
        out.append(_step("대화방으로 보내기", send))
    return out


def telegram_find_chat(session=None) -> dict:
    """내 봇에게 보낸 메시지에서 대화방 번호를 찾는다 (다른 봇 없이). 텔레그램은 최근 24시간 메시지만 알려 준다."""
    http = session or requests
    tok = vault.get("telegram_bot_token")
    if not tok:
        raise RuntimeError("먼저 '2. 연결'에 텔레그램 봇 토큰을 저장해 주세요")
    me = http.get(f"https://api.telegram.org/bot{tok}/getMe", timeout=8).json()
    if not me.get("ok"):
        raise RuntimeError("봇 토큰이 맞지 않아요. @BotFather 가 준 토큰을 다시 복사해 저장해 주세요")
    bot = "@" + me["result"]["username"]
    d = http.get(f"https://api.telegram.org/bot{tok}/getUpdates", timeout=10, params={"limit": 100}).json()
    if not d.get("ok"):
        raise RuntimeError(f"텔레그램 응답: {d.get('description', '알 수 없는 오류')}")
    chats = {}
    for u in d.get("result", []):
        msg = u.get("message") or u.get("edited_message") or (u.get("my_chat_member") or {})
        ch = msg.get("chat") or {}
        if ch.get("type") == "private" and ch.get("id"):
            chats[ch["id"]] = {"chat_id": str(ch["id"]), "name": " ".join(x for x in (ch.get("first_name"), ch.get("last_name")) if x) or ch.get("username") or ""}
    if not chats:
        raise RuntimeError(f"아직 {bot} 에게 온 메시지가 없어요. 텔레그램에서 {bot} 를 열어 '시작'을 누르고 아무 말이나 보낸 뒤 다시 눌러 주세요")
    found = list(chats.values())[-1]  # 가장 최근에 말을 건 사람
    return found | {"bot": bot, "count": len(chats)}


TESTS = {"toss": toss, "anthropic": anthropic, "dart": dart, "telegram": telegram}
