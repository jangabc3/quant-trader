"""휴대폰(텔레그램) 명령: 가짜 텔레그램 서버로 시험한다."""
import time

from qtrader import checks, config as config_mod, telegram_bot
from qtrader.storage import Store

TOK = "123:ABC"
ME = "555"


class R:
    def __init__(self, d):
        self.d = d

    def json(self):
        return self.d


class FakeTelegram:
    """getUpdates 는 쌓인 메시지를 offset 에 맞춰 돌려주고, sendMessage 는 받은 글을 적어 둔다."""

    def __init__(self):
        self.updates, self.sent, self.calls = [], [], []
        self.next_id = 100

    def say(self, chat_id, text, first_name="길동", age=0):
        self.updates.append({"update_id": self.next_id, "message": {
            "date": int(time.time() - age), "text": text,
            "chat": {"id": int(chat_id), "type": "private", "first_name": first_name}}})
        self.next_id += 1

    def post(self, url, json=None, **k):
        method = url.rsplit("/", 1)[-1]
        self.calls.append((method, json))
        if method == "getUpdates":
            off = (json or {}).get("offset", 0)
            return R({"ok": True, "result": [u for u in self.updates if u["update_id"] >= off]})
        if method == "sendMessage":
            self.sent.append(json)
            return R({"ok": True})
        return R({"ok": False})

    def get(self, url, params=None, **k):  # checks.telegram_find_chat
        if url.endswith("/getMe"):
            return R({"ok": True, "result": {"username": "my_qtrader_bot"}})
        return R({"ok": True, "result": []})  # 폴링이 이미 받아 가서 비어 있음


def _setup(fake_vault, chat=ME):
    fake_vault["telegram_bot_token"] = TOK
    cfg = config_mod.load()
    cfg.setdefault("notify", {})["telegram_chat_id"] = chat
    config_mod.save(cfg)
    store = Store(config_mod.home() / "qtrader.db")
    return store


def test_stop_from_my_chat_turns_kill_switch_on(fake_vault):
    store = _setup(fake_vault)
    store.set("auto_trading", "1")
    tg = FakeTelegram()
    p = telegram_bot.Poller(session=tg, store=store)
    tg.say(ME, "/stop")
    assert p.poll_once(0) == 1
    assert store.get("kill_switch") == "1" and store.get("kill_reason") == "휴대폰(텔레그램)에서 켬"
    assert store.get("auto_trading") == "1"  # 긴급 정지는 새 매수만 막는다
    assert tg.sent[-1]["chat_id"] == ME and "긴급 정지를 켰어요" in tg.sent[-1]["text"]
    assert any(e["level"] == "notice" and e["source"] == "telegram" for e in store.events())
    # offset 이 넘어가서 같은 명령을 두 번 따르지 않는다
    assert store.get("tg_offset") == "101" and p.poll_once(0) == 0
    # /pause 는 자동매매를 끈다
    tg.say(ME, "/pause@my_qtrader_bot")
    p.poll_once(0)
    assert store.get("auto_trading") == "0" and "자동매매를 껐어요" in tg.sent[-1]["text"]


def test_other_chat_is_ignored_silently(fake_vault):
    store = _setup(fake_vault)
    tg = FakeTelegram()
    p = telegram_bot.Poller(session=tg, store=store)
    tg.say("999", "/stop", first_name="남")
    tg.say("999", "/pause", first_name="남")
    assert p.poll_once(0) == 0
    assert store.get("kill_switch", "0") == "0" and tg.sent == []
    # 그래도 대화방 기록은 남겨 '자동으로 찾기'가 쓸 수 있다
    assert [c["chat_id"] for c in telegram_bot.recorded_chats(store)] == ["999"]


def test_stale_command_is_not_followed(fake_vault):
    store = _setup(fake_vault)
    tg = FakeTelegram()
    tg.say(ME, "/stop", age=3600)  # 서버가 꺼져 있던 동안 쌓인 옛 명령
    telegram_bot.Poller(session=tg, store=store).poll_once(0)
    assert store.get("kill_switch", "0") == "0"


def test_status_reply(fake_vault):
    store = _setup(fake_vault)
    store.set("auto_trading", "1")
    tg = FakeTelegram()
    tg.say(ME, "/status")
    telegram_bot.Poller(session=tg, store=store).poll_once(0)
    t = tg.sent[-1]["text"]
    assert t.startswith("[QTrader] 지금 상태")
    assert "모드: 모의투자 (가상 돈)" in t and "자동매매: 켜짐" in t and "긴급 정지: 꺼짐" in t
    assert "국내: 평가" in t and "보유 0종목" in t and "다음 실행" in t and "오늘" in t
    assert TOK not in t


def test_turn_on_commands_are_refused(fake_vault):
    store = _setup(fake_vault)
    store.set("kill_switch", "1")
    tg = FakeTelegram()
    for cmd in ("/resume", "/live", "/buy 005930"):
        tg.say(ME, cmd)
    telegram_bot.Poller(session=tg, store=store).poll_once(0)
    assert store.get("kill_switch") == "1" and store.get("auto_trading", "0") == "0"
    assert all("PC에서 켜 주세요" in s["text"] for s in tg.sent) and len(tg.sent) == 3
    assert "/status" in telegram_bot.handle(store, {}, "/help")


def test_no_token_or_chat_does_nothing(fake_vault):
    tg = FakeTelegram()
    tg.say(ME, "/stop")
    store = Store(config_mod.home() / "qtrader.db")
    p = telegram_bot.Poller(session=tg, store=store)
    assert p.poll_once(0) == -1 and tg.calls == []  # 토큰 없음
    fake_vault["telegram_bot_token"] = TOK  # 토큰은 있지만 대화방 번호 없음
    assert p.poll_once(0) == -1 and tg.calls == []
    assert store.get("kill_switch", "0") == "0"


def test_find_chat_uses_recorded_chats(fake_vault):
    store = _setup(fake_vault)
    tg = FakeTelegram()
    tg.say("777", "안녕", first_name="새폰")
    telegram_bot.Poller(session=tg, store=store).poll_once(0)
    # 폴링이 메시지를 받아 가서 getUpdates 는 비었지만 기록에서 찾는다
    r = checks.telegram_find_chat(tg, store=store)
    assert r["chat_id"] == "777" and r["name"] == "새폰" and r["bot"] == "@my_qtrader_bot"
