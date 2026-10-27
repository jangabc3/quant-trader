"""휴대폰(텔레그램)에서 내 QTrader 에 보내는 명령.

안전을 위해 멈추는 쪽만 받는다: /status 상태 보기, /stop 긴급 정지, /pause 자동매매 끄기, /help.
다시 켜기·긴급 정지 끄기·실전 전환·주문은 PC 화면에서만 한다.
저장된 대화방(notify.telegram_chat_id)에서 온 메시지만 따르고, 다른 대화방은 말없이 무시한다.
봇 토큰이나 대화방 번호가 없으면 텔레그램에 아무 요청도 하지 않는다.

getUpdates 로 받으면 offset 이 넘어가서 '대화방 번호 자동으로 찾기'가 메시지를 못 본다.
그래서 여기서 본 개인 대화방을 state(tg_chats)에 적어 두고 checks.telegram_find_chat 이 함께 쓴다.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone

import requests

from . import config as config_mod, vault
from .models import Market
from .storage import Store

API = "https://api.telegram.org/bot{tok}/{method}"
LONG_POLL = 25  # 초. 텔레그램이 새 메시지를 기다려 주는 시간
STALE_SEC = 600  # 서버가 꺼져 있던 동안 쌓인 오래된 명령은 따르지 않는다
MODE_KO = {"paper": "모의투자 (가상 돈)", "toss_real": "실전 (토스증권, 진짜 돈)",
           "kis_demo": "한국투자증권 모의투자", "kis_real": "실전 (한국투자증권, 진짜 돈)"}
HELP = ("QTrader 명령\n"
        "/status 지금 상태 (모드·자동매매·긴급 정지·오늘 손익·보유 종목·다음 실행)\n"
        "/stop 긴급 정지 (새로 사기만 멈춰요. 손절·익절 매도는 계속해요)\n"
        "/pause 자동매매 끄기\n"
        "/help 이 안내\n"
        "안전을 위해 다시 켜기·실전 전환·주문은 PC 화면에서만 할 수 있어요.")
# 켜는 쪽 명령: 휴대폰으로는 받지 않는다
TURN_ON = {"resume", "start_auto", "auto", "on", "unstop", "unkill", "kill_off", "live", "real",
           "buy", "sell", "order", "run", "go", "재개", "켜기", "시작", "실전", "매수", "매도", "주문"}


def _store() -> Store:
    return Store(config_mod.home() / "qtrader.db")


def recorded_chats(store: Store) -> list[dict]:
    """이 봇에게 말을 건 개인 대화방 기록 (오래된 것부터)."""
    try:
        d = json.loads(store.get("tg_chats") or "{}")
    except ValueError:
        return []
    return sorted(d.values(), key=lambda c: c.get("last", ""))


def record_chat(store: Store, chat: dict) -> None:
    if chat.get("type") != "private" or not chat.get("id"):
        return
    try:
        d = json.loads(store.get("tg_chats") or "{}")
    except ValueError:
        d = {}
    cid = str(chat["id"])
    name = " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x) or chat.get("username") or ""
    d[cid] = {"chat_id": cid, "name": name, "last": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    if len(d) > 20:
        d = dict(sorted(d.items(), key=lambda kv: kv[1]["last"])[-20:])
    store.set("tg_chats", json.dumps(d, ensure_ascii=False))


def _money(m: Market, v: float) -> str:
    return f"{v:,.0f}원" if m == Market.KR else f"${v:,.2f}"


def status_text(store: Store, cfg: dict) -> str:
    from .engine import build_context, trade_date
    from .scheduler import next_run
    lines = ["지금 상태",
             f"모드: {MODE_KO.get(cfg['mode'], cfg['mode'])}",
             f"자동매매: {'켜짐' if store.get('auto_trading', '0') == '1' else '꺼짐'}"]
    if store.get("kill_switch", "0") == "1":
        lines.append("긴급 정지: 켜짐 (새로 사기 멈춤, 손절·익절 매도는 계속)")
    else:
        lines.append("긴급 정지: 꺼짐")
    try:
        c = build_context(cfg, store)
    except Exception as e:
        lines.append(f"계좌를 읽지 못했어요: {e}")
        return "\n".join(lines)
    for m in (Market.KR, Market.US):
        mc = cfg["markets"][m.value]
        if not mc.get("enabled"):
            continue
        name = "국내" if m == Market.KR else "미국"
        nxt = next_run(m, mc["run_time"]).strftime("%m/%d %H:%M")
        try:
            a = c.broker.account(m)
        except Exception as e:
            lines.append(f"{name}: 계좌를 읽지 못했어요 ({type(e).__name__}), 다음 실행 {nxt}")
            continue
        base = store.day_start_equity(c.broker.name, m.value, trade_date(m))
        if base:
            d = a.equity - base
            day = f"{d / base:+.1%} ({'+' if d >= 0 else '-'}{_money(m, abs(d))})"
        else:
            dp = [p.day_pnl for p in a.positions.values() if p.day_pnl is not None]
            day = (("+" if sum(dp) >= 0 else "-") + _money(m, abs(sum(dp)))) if dp else "아직 기준 없음"
        lines.append(f"{name}: 평가 {_money(m, a.equity)}, 오늘 {day}, 보유 {len(a.positions)}종목, 다음 실행 {nxt}")
    return "\n".join(lines)


def handle(store: Store, cfg: dict, text: str) -> str | None:
    """허용된 대화방의 메시지 하나를 처리하고 답장 글을 돌려준다."""
    text = (text or "").strip()
    if not text.startswith("/"):
        return HELP
    cmd = text[1:].split()[0].split("@")[0].lower() if len(text) > 1 else ""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if cmd in ("help", "start", "도움말"):
        return HELP
    if cmd in ("status", "상태"):
        store.log("notice", "telegram", "휴대폰(텔레그램)에서 상태를 확인했어요")
        return status_text(store, cfg)
    if cmd in ("stop", "kill", "정지"):
        if store.get("kill_switch", "0") == "1":
            store.log("notice", "telegram", "휴대폰(텔레그램)에서 긴급 정지 요청 (이미 켜져 있음)")
            return "긴급 정지는 이미 켜져 있어요. 새로 사지 않고, 손절·익절 매도는 계속해요."
        store.set("kill_switch", "1")
        store.set("kill_since", now)
        store.set("kill_reason", "휴대폰(텔레그램)에서 켬")
        store.log("notice", "telegram", "휴대폰(텔레그램)에서 긴급 정지를 켰어요")
        return "긴급 정지를 켰어요. 이제 새로 사지 않아요. 손절·익절 매도는 계속해요.\n끄는 것은 PC 화면에서 해 주세요."
    if cmd in ("pause", "중지"):
        if store.get("auto_trading", "0") != "1":
            store.log("notice", "telegram", "휴대폰(텔레그램)에서 자동매매 끄기 요청 (이미 꺼져 있음)")
            return "자동매매는 이미 꺼져 있어요."
        store.set("auto_trading", "0")
        store.log("notice", "telegram", "휴대폰(텔레그램)에서 자동매매를 껐어요")
        return "자동매매를 껐어요. 다시 켜는 것은 PC 화면에서 해 주세요."
    if cmd in TURN_ON:
        store.log("notice", "telegram", f"휴대폰(텔레그램)에서 /{cmd} 요청 — 켜는 쪽은 PC에서만 해요")
        return "안전을 위해 다시 켜기·실전 전환·주문은 휴대폰으로 할 수 없어요. PC에서 켜 주세요."
    return "모르는 명령이에요.\n\n" + HELP


class Poller:
    """getUpdates 롱 폴링을 따로 도는 데몬 스레드에서 돌린다."""

    def __init__(self, session=None, store: Store | None = None, load_cfg=None, idle: float = 30.0):
        self.http = session or requests.Session()
        self._store = store
        self.load_cfg = load_cfg or config_mod.load
        self.idle = idle
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_err = ""

    @property
    def store(self) -> Store:
        if self._store is None:
            self._store = _store()
        return self._store

    def _call(self, tok: str, method: str, wait: float, payload: dict) -> dict:
        r = self.http.post(API.format(tok=tok, method=method), json=payload, timeout=wait)
        return r.json()

    def send(self, tok: str, chat: str, text: str) -> None:
        try:
            self._call(tok, "sendMessage", 10, {"chat_id": chat, "text": f"[QTrader] {text}"})
        except requests.RequestException as e:
            self.store.log("warn", "telegram", f"텔레그램 답장 실패: {type(e).__name__}")

    def poll_once(self, long_poll: int = LONG_POLL) -> int:
        """한 번 받아서 처리. 처리한 메시지 수. 토큰·대화방 번호가 없으면 아무 요청도 하지 않고 -1."""
        tok = vault.get("telegram_bot_token")
        cfg = self.load_cfg()
        allowed = str((cfg.get("notify") or {}).get("telegram_chat_id") or "").strip()
        if not tok or not allowed:
            return -1
        store = self.store
        params = {"timeout": long_poll, "allowed_updates": ["message"]}
        off = store.get("tg_offset")
        if off:
            params["offset"] = int(off)
        d = self._call(tok, "getUpdates", long_poll + 10, params)
        if not d.get("ok"):
            raise RuntimeError(d.get("description") or "getUpdates 실패")
        done = 0
        for u in d.get("result", []):
            store.set("tg_offset", str(int(u["update_id"]) + 1))  # 먼저 넘겨 두어 같은 명령을 두 번 따르지 않는다
            msg = u.get("message") or {}
            chat = msg.get("chat") or {}
            record_chat(store, chat)
            if str(chat.get("id")) != allowed:
                continue  # 다른 대화방은 말없이 무시
            if time.time() - float(msg.get("date") or 0) > STALE_SEC:
                continue  # 서버가 꺼져 있던 동안 쌓인 옛 명령
            reply = handle(store, cfg, msg.get("text") or "")
            if reply:
                self.send(tok, allowed, reply)
            done += 1
        return done

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="qtrader-telegram", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        wait = 5.0
        while not self._stop.is_set():
            try:
                if self.poll_once() < 0:
                    self._stop.wait(self.idle)  # 토큰·대화방 번호가 생기면 그때부터 받는다
                wait, self._last_err = 5.0, ""
            except Exception as e:
                tok = vault.get("telegram_bot_token") or ""
                msg = f"{type(e).__name__}: {e}"
                msg = (msg.replace(tok, "***") if tok else msg)[:200]  # 연결 오류 글에 주소(토큰)가 섞일 수 있다
                if msg != self._last_err:  # 같은 오류로 기록을 채우지 않는다
                    self.store.log("warn", "telegram", f"텔레그램 명령 받기 오류: {msg}")
                    self._last_err = msg
                self._stop.wait(wait)
                wait = min(wait * 2, 120.0)
