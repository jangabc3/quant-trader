"""시장별 실행 시각에 하루 한 번 엔진을 돌리고, 장중에는 2분마다 감시·손절·익절과 (켜면) 장중 회의 -> 한 종목 매수를 한다.
자동매매가 켜져 있을 때만."""
from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace
from datetime import datetime, time as dtime, timedelta

from . import config as config_mod
from .engine import TZ, build_context, run_market, trade_date
from .models import Market

# 정규장 (현지 시각)
SESSIONS = {Market.KR: (dtime(9, 0), dtime(15, 30)), Market.US: (dtime(9, 30), dtime(16, 0))}


def parse_hm(s: str) -> dtime:
    h, m = s.split(":")
    return dtime(int(h), int(m))


def is_open(market: Market, now: datetime | None = None) -> bool:
    t = (now or datetime.now(TZ[market])).astimezone(TZ[market])
    o, c = SESSIONS[market]
    return t.weekday() < 5 and o <= t.time() < c


def after_close(market: Market, now: datetime | None = None) -> bool:
    """평일 장 마감 5분 뒤부터 자정 전까지."""
    t = (now or datetime.now(TZ[market])).astimezone(TZ[market])
    close = SESSIONS[market][1]
    return t.weekday() < 5 and t.time() >= dtime(close.hour, close.minute + 5)


def _hm_range(s: str) -> tuple[dtime, dtime]:
    a, b = s.split("-")
    return parse_hm(a), parse_hm(b)


def trading_now(cfg: dict, market: Market, now: datetime | None = None) -> bool:
    """정규장 + 설정에서 켠 시간외 세션까지 (손절·익절 감시와 주문이 가능한 시간).
    국내: 넥스트레이드(NXT) 08:00~20:00. 미국: 주간거래(한국 낮), 프리마켓(뉴욕 04:00~09:30), 애프터마켓(16:00~20:00)."""
    if is_open(market, now):
        return True
    mc = cfg["markets"][market.value]
    kst = (now or datetime.now(TZ[Market.KR])).astimezone(TZ[Market.KR])
    if market == Market.KR:
        return bool(mc.get("extended")) and kst.weekday() < 5 and dtime(8, 0) <= kst.time() < dtime(20, 0)
    ext = mc.get("extended_sessions") or []
    if "day" in ext and kst.weekday() < 5:
        a, b = _hm_range(mc.get("day_hours", "10:00-17:30"))
        if a <= kst.time() < b:
            return True
    ny = kst.astimezone(TZ[Market.US])
    if ny.weekday() < 5 and (("pre" in ext and dtime(4, 0) <= ny.time() < dtime(9, 30))
                             or ("after" in ext and dtime(16, 0) <= ny.time() < dtime(20, 0))):
        return True
    return False


def session_name(cfg: dict, market: Market, now: datetime | None = None) -> str | None:
    """지금 감시·주문하는 세션 이름 (화면용). 쉬는 시간이면 None."""
    if is_open(market, now):
        return "정규장"
    if not trading_now(cfg, market, now):
        return None
    if market == Market.KR:
        return "넥스트레이드"
    ny = (now or datetime.now(TZ[Market.US])).astimezone(TZ[Market.US])
    if ny.weekday() < 5 and dtime(4, 0) <= ny.time() < dtime(9, 30) and "pre" in (cfg["markets"]["US"].get("extended_sessions") or []):
        return "프리마켓"
    if ny.weekday() < 5 and dtime(16, 0) <= ny.time() < dtime(20, 0) and "after" in (cfg["markets"]["US"].get("extended_sessions") or []):
        return "애프터마켓"
    return "주간거래"


def run_clock(cfg: dict, market: Market):
    """정기 실행 시각을 어느 시계로 볼지: 미국을 '주간거래'로 고르면 한국 시각, 아니면 그 나라 시각."""
    mc = cfg["markets"][market.value]
    if market == Market.US and mc.get("run_session") == "day":
        a, b = _hm_range(mc.get("day_hours", "10:00-17:30"))
        return TZ[Market.KR], b
    return TZ[market], SESSIONS[market][1]


def due(market: Market, run_time: str, now: datetime | None = None, cfg: dict | None = None) -> bool:
    """오늘 실행 시각이 지났고 아직 장중이면 True. 주말은 False. (휴장일은 증권사 거부로 걸러진다)"""
    tz, end = run_clock(cfg, market) if cfg else (TZ[market], SESSIONS[market][1])
    t = (now or datetime.now(tz)).astimezone(tz)
    return t.weekday() < 5 and parse_hm(run_time) <= t.time() < end


def next_run(market: Market, run_time: str, now: datetime | None = None) -> datetime:
    t = (now or datetime.now(TZ[market])).astimezone(TZ[market])
    cand = t.replace(hour=parse_hm(run_time).hour, minute=parse_hm(run_time).minute, second=0, microsecond=0)
    if cand <= t:
        cand += timedelta(days=1)
    while cand.weekday() >= 5:
        cand += timedelta(days=1)
    return cand


_picking: set[str] = set()
_picking_lock = threading.Lock()


def todays_later(ctx, m: Market, today: str, n: int) -> list[dict]:
    """오늘 후보가 이미 있으면 바로, 없으면 뒤에서 고르기 시작하고 빈 목록 (몇십 초~몇 분 걸리는 일이라
    점검이 그동안 손절·익절 감시를 멈추지 않게). 다음 점검 때 다 골라져 있으면 그걸 쓴다."""
    import json as _json
    from .screener import todays
    saved = ctx.store.get(f"picks_{m.value}_{today}")
    if saved:
        return _json.loads(saved)
    with _picking_lock:
        if m.value in _picking:
            return []
        _picking.add(m.value)

    def work():
        try:
            from .engine import build_context
            todays(build_context(), m, today, n)  # 이 스레드 전용 연결
        except Exception as e:
            try:
                ctx.store.log("warn", f"picks.{m.value}", f"오늘 후보 고르기 실패: {e}")
            except Exception:
                pass
        finally:
            with _picking_lock:
                _picking.discard(m.value)
    threading.Thread(target=work, name=f"picks-{m.value}", daemon=True).start()
    return []


def mark_planned_stop(store, why: str, at: str | None = None) -> None:
    """일부러 끈다고 적어 둔다 (why: update = 업데이트, quit = 사용자가 끄기). 다시 켰을 때 '꺼져 있었어요' 경고를 띄우지 않는다."""
    from datetime import timezone as _tz
    store.set("planned_stop", json.dumps({"why": why, "at": at or datetime.now(_tz.utc).isoformat(timespec="seconds")}))


class Scheduler:
    def __init__(self, interval: float = 20.0):
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_error = ""
        self._last_exit = 0.0
        self._last_alert = 0.0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="qtrader-scheduler", daemon=True)
        self._thread.start()
        # '살아 있음' 표시는 점검과 따로 20초마다 (점검 한 번이 오래 걸려도 꺼진 것으로 잘못 알리지 않게)
        threading.Thread(target=self._beat_loop, name="qtrader-heartbeat", daemon=True).start()

    def _beat_loop(self) -> None:
        from .storage import Store
        store = Store(config_mod.home() / "qtrader.db")
        try:  # 설치된 버전이 지난번과 다르면 그 사이 꺼져 있던 건 업데이트 때문 (설치.cmd 를 직접 눌러 다시 깐 경우도)
            from . import updater
            rev = (updater.source_info(config_mod.home()) or {}).get("rev") or ""
            seen = store.get("seen_rev") or ""
            if rev and seen and rev != seen:
                mark_planned_stop(store, "update", at="1970-01-01T00:00:00+00:00")
            if rev:
                store.set("seen_rev", rev)
        except Exception:
            pass
        while not self._stop.is_set():
            try:
                cfg = config_mod.load()
                self._heartbeat(SimpleNamespace(store=store), cfg, store.get("auto_trading", "0") == "1")
            except Exception:
                pass
            self._stop.wait(20)

    def stop(self) -> None:
        self._stop.set()

    def tick(self) -> list[dict]:
        cfg = config_mod.load()
        ctx = build_context(cfg)
        from . import extras
        if cfg.get("alerts") and time.monotonic() - self._last_alert > 60:
            self._last_alert = time.monotonic()
            try:
                extras.check_alerts(ctx.store, cfg, lambda s: ctx.data.price(Market.KR if s[:1].isdigit() else Market.US, s),
                                    config_mod.save)
            except Exception as e:
                ctx.store.log("warn", "alert", f"가격 알림 확인 실패: {e}")
        # 자산 기록은 자동매매가 꺼져 있어도 장 마감 뒤 하루 한 번 남긴다 (자산 추이 그래프용)
        if ctx.store.get("auto_trading", "0") != "1" and time.monotonic() - getattr(self, "_last_snap", 0) > 600:
            self._last_snap = time.monotonic()
            from .engine import snapshot_equity
            for m in (Market.KR, Market.US):
                if cfg["markets"][m.value].get("enabled") and after_close(m):
                    try:
                        snapshot_equity(ctx, m)
                    except Exception:
                        pass
        auto = ctx.store.get("auto_trading", "0") == "1"
        mk = cfg["markets"]["KR"]
        if mk.get("enabled") and mk.get("strategy") == "ai" and cfg["ai"].get("auto_pick", True):
            from . import scan  # 국내 전체 종목을 하루 한 번 뒤에서 훑는다 (자동매매가 꺼져 있어도 후보는 준비)
            try:
                scan.maybe_start(ctx, Market.KR, trade_date(Market.KR))
            except Exception as e:
                ctx.store.log("warn", "picks.KR", f"전체 종목 훑기 시작 실패: {e}")
        extras.keep_awake(auto)
        if not auto:
            return []
        out = []
        if cfg["ai"].get("auto_exit", True) and time.monotonic() - self._last_exit > 120:
            self._last_exit = time.monotonic()
            from .engine import check_exits, check_loss, snapshot_equity, sync_orders
            try:
                for o in sync_orders(ctx):
                    if o["status"] == "filled":
                        extras.notify(ctx.store, cfg, f"✅ {o['symbol']} {o['note']}", "order")
            except Exception as e:
                ctx.store.log("error", "sync", f"체결 확인 실패: {e}")
            try:  # 분할·병합 반영 (보호를 맞추기 전에 가격을 먼저 고친다), DART 기업 이벤트는 하루 한 번
                from . import corp
                corp.check(ctx)
                corp.dart_scan(ctx)
            except Exception as e:
                ctx.store.log("warn", "corp", f"기업 이벤트 확인 실패: {e}")
            try:  # 토스 서버 손절·익절 보호(OCO) 맞추기
                from . import guard
                guard.sync(ctx)
            except Exception as e:
                ctx.store.log("warn", "guard", f"토스 보호 확인 실패: {e}")
            try:  # 가상매매 조건 주문 (토스는 토스가 직접 감시)
                from . import conditional
                for r in conditional.check(ctx):
                    extras.notify(ctx.store, cfg, f"🎯 조건 주문 실행: {r['symbol']} {r['qty']}주 {'매수' if r['side'] == 'buy' else '매도'} ({r['trigger']:,} 도달)", "order")
            except Exception as e:
                ctx.store.log("warn", "cond", f"조건 주문 확인 실패: {e}")
            for m in (Market.KR, Market.US):
                if not cfg["markets"][m.value].get("enabled"):
                    continue
                try:
                    if trading_now(cfg, m):
                        was_killed = ctx.kill
                        if check_loss(ctx, m) and not was_killed:
                            extras.notify(ctx.store, cfg, f"⛔ {m.value} 하루 손실 한도를 넘어 신규 매수를 멈췄습니다", "risk")
                        if (cfg.get("compare") or {}).get("enabled"):
                            from . import compare
                            compare.watch(ctx, m)
                        for x in check_exits(ctx, m):
                            head = {"stop": "🛑 손절", "take": "🎯 익절", "half": "🎯 절반 익절", "time": "⏱ 기간 정리"}.get(x["reason"], "💸 매도")
                            extras.notify(ctx.store, cfg, f"{head} {x['symbol']} @ {x['price']:,} ({x['status']})", "exit")
                        if cfg["markets"][m.value].get("strategy") == "ai":
                            from . import live
                            rk = f"_recheck_{m.value}"
                            if time.monotonic() - getattr(self, rk, float("-inf")) >= 60 * int(cfg["ai"].get("recheck_every_min", 2)) - 5:
                                setattr(self, rk, time.monotonic())
                                live.run_rechecks(ctx, m)
                            self._new_candidates(ctx, m)
                            self._intraday(ctx, m)
                    if after_close(m) and (cfg.get("compare") or {}).get("enabled"):
                        from . import compare
                        compare.snapshot(ctx, m)
                    if after_close(m) and snapshot_equity(ctx, m):
                        today = trade_date(m)
                        rep = extras.daily_report(ctx.store, ctx.broker.name, m.value, today)
                        ctx.store.set(f"report_{m.value}", json.dumps(rep, ensure_ascii=False, default=str))
                        if (cfg.get("notify") or {}).get("daily_report", True):
                            extras.notify(ctx.store, cfg, rep["text"], "report")
                except Exception as e:
                    ctx.store.log("error", f"exit.{m.value}", f"장중 점검 실패: {e}")
        for m in (Market.KR, Market.US):
            mc = cfg["markets"].get(m.value, {})
            if mc.get("enabled") and mc.get("strategy") == "ai" and cfg["ai"].get("auto_meetings", False):
                self._ai_meetings(ctx, m, mc)  # 정기 실행 전 7명 회의 (기본 꺼짐: 사는 신호 + 거부권이 대신한다)
            if not mc.get("enabled") or not due(m, mc["run_time"], cfg=cfg):
                continue
            if not (m == Market.US and mc.get("run_session") == "day") and not self._session_open(ctx, m):
                ctx.store.log("info", f"scheduler.{m.value}", "휴장일이라 실행하지 않습니다")
                ctx.store.mark_run(trade_date(m), ctx.broker.name, m.value)
                continue
            if ctx.store.ran(trade_date(m), ctx.broker.name, m.value):
                continue
            try:
                res = run_market(ctx, m)
                out.append(res)
                if (cfg.get("compare") or {}).get("enabled") and mc.get("strategy") == "ai":
                    from . import compare
                    compare.run(ctx, m)
                for o in res.get("orders", []):
                    if o.get("status") in ("filled", "submitted"):
                        why = (ctx.store.rows("select message from orders where symbol=? and trade_date=? order by id desc limit 1",
                                              (o["symbol"], trade_date(m))) or [{}])[0].get("message", "")
                        extras.notify(ctx.store, cfg, f"{'🛒 매수' if o['side'] == 'buy' else '💸 매도'} {o['symbol']} {o['qty']}주 — "
                                      + " · ".join(why.split(" | ")[:2]), "order")
            except Exception as e:
                ctx.store.log("error", f"scheduler.{m.value}", f"실행 실패: {e}")
                # 같은 날 계속 재시도하며 주문이 쌓이지 않도록 실패도 실행으로 기록
                ctx.store.mark_run(trade_date(m), ctx.broker.name, m.value)
        return out

    def _heartbeat(self, ctx, cfg, auto: bool) -> None:
        """20초마다(_beat_loop) 살아 있다고 적어 둔다. 장중에 10분 넘게 비어 있었으면 (PC·프로그램이 꺼졌거나 PC 가 잠들었던 것)
        알리고 바로 다시 점검한다. 점검(tick)이 오래 걸리는 것과는 상관없다 (따로 돈다)."""
        from datetime import timezone as _tz
        from . import extras
        now = datetime.now(_tz.utc)
        last = ctx.store.get("heartbeat")
        ctx.store.set("heartbeat", now.isoformat(timespec="seconds"))
        planned = json.loads(ctx.store.get("planned_stop") or "null")
        if planned:  # 켜진 뒤 첫 심장박동에서만 쓰고 지운다 (남아 있으면 나중의 진짜 꺼짐을 가릴 수 있다)
            ctx.store.exec("delete from state where key='planned_stop'")
        if not last or not auto:
            return
        prev = datetime.fromisoformat(last)
        gap = (now - prev).total_seconds()
        if gap < 600:
            return
        if planned and planned.get("at", "") >= "1970-01-02" and planned["at"] < (prev - timedelta(minutes=2)).isoformat():
            planned = None  # 이번 꺼짐보다 한참 전에 적힌 것
        hit = [m for m in (Market.KR, Market.US) if cfg["markets"][m.value].get("enabled") and (is_open(m, prev) or is_open(m, now))]
        if not hit:
            return
        loc = lambda t: t.astimezone().strftime("%m-%d %H:%M")
        if planned and planned.get("why") in ("update", "quit"):  # 일부러 끈 것: 경고 대신 (업데이트면) 안내만
            self._last_exit = 0.0  # 그래도 곧바로 체결 확인·손절 점검
            if planned["why"] == "update":
                extras.notify(ctx.store, cfg, f"🔄 업데이트를 마치고 다시 켜졌어요 ({int(gap // 60)}분 걸렸어요). "
                                              f"지금 바로 체결과 손절·익절을 다시 확인해요.", "live")
            else:
                ctx.store.log("info", "live", f"직접 끈 뒤 다시 켜졌어요 ({loc(prev)} ~ {loc(now)})")
            return
        extras.notify(ctx.store, cfg, f"⚠️ 프로그램이 {int(gap // 60)}분 동안 꺼져 있었어요 ({loc(prev)} ~ {loc(now)}). "
                                      f"그동안 손절·익절 감시가 멈췄습니다. 지금 바로 다시 확인합니다.", "live")
        self._last_exit = 0.0  # 곧바로 체결 확인·손절 점검

    def _new_candidates(self, ctx, m: Market) -> None:
        """N분마다(ai.intraday_every_min, 기본 15) 숫자 규칙을 다시 돌려, 새로 통과한 종목을 오늘 회의 후보에 올린다.
        장중 계속(ai.intraday)을 켜면 _intraday 가 이 후보로 회의를 열고 바로 산다. 끄면 정기 실행 때만 검토한다."""
        import json as _json
        from . import live
        key = f"_cand_{m.value}"
        every = 60 * int(ctx.cfg["ai"].get("intraday_every_min", 15))
        if time.monotonic() - getattr(self, key, float("-inf")) < every:
            return
        setattr(self, key, time.monotonic())
        today = trade_date(m)
        pk = f"picks_{m.value}_{today}"
        picks = _json.loads(ctx.store.get(pk) or "[]")
        have = {p["symbol"] for p in picks}
        from . import principles, screener
        allowed = int((principles.DEFAULT_RULES | principles.rules_for(ctx.cfg)).get("soft_miss_allowed", 1))
        fresh = [c for c in live.next_candidates(ctx, m, ttl=every)
                 if screener.passes(c["missing"], allowed) and c["symbol"] not in have]  # 후보 거르기와 같은 필수 + 추가 규칙
        from .screener import CANDIDATES_SHOWN
        cap = max(config_mod.meetings_cap(ctx.cfg, m), CANDIDATES_SHOWN)  # 후보 목록 길이 (회의 수 한도는 _intraday 가 본다)
        if fresh and len(picks) < cap:
            add = fresh[: cap - len(picks)]
            picks += [{"symbol": c["symbol"], "score": c["score"], "reason": "장중 새로 규칙 통과", "held": False, "fails": []} for c in add]
            ctx.store.set(pk, _json.dumps(picks, ensure_ascii=False))
            when = "2분마다 사는 신호 확인" if ctx.cfg["ai"].get("intraday", True) else "장중 매수가 꺼져 있어 지켜보기만"
            ctx.store.log("info", f"live.{m.value}", "새 후보: " + ", ".join(c["symbol"] for c in add) + f" ({when})")

    def _intraday(self, ctx, m: Market) -> list[dict]:
        """장중 계속 사기 (ai.intraday): 2분마다 오늘 후보의 실시간 가격으로 사는 신호(signals.check)를 보고,
        신호가 뜬 종목만 AI 거부권(베어리, Claude 1번)을 거쳐 그 종목만 산다 (engine.buy_one: 숫자 안전 점검·리스크 검사).
        비용 한도: 시장별 하루 거부권 확인 수(markets.*.meetings_per_day)·월 비용 상한, 같은 종목은 하루 한 번, 한 틱에 한 종목.
        7명 회의는 사용자가 직접 열 때만 (자동 매수하지 않는다)."""
        import json as _json
        from . import extras, signals
        from .engine import buy_blocked, buy_one

        ai = ctx.cfg["ai"]
        if not ai.get("intraday", True):
            return []
        today = trade_date(m)
        key = f"intraday_{m.value}_{today}"
        state = _json.loads(ctx.store.get(key) or "{}")
        for e in state.values():  # 예전 방식(장중 회의)으로 남은 기록: 결과는 회의 보기로, 자동으로 사지 않는다
            if e.get("state") == "meeting":
                e.update(state="skipped", note="예전 방식 회의 기록 (자동 매수 안 함)")
        out: list[dict] = []
        if ctx.kill:
            return out
        why = buy_blocked(ctx, m)
        if why:  # 오늘은 더 못 사므로 신호도 보지 않는다 (기록은 하루 한 번)
            if ctx.store.get(f"intraday_skip_{m.value}_{today}") != why:
                ctx.store.set(f"intraday_skip_{m.value}_{today}", why)
                ctx.store.log("info", f"intraday.{m.value}", f"장중 매수 쉼: {why}")
            ctx.store.set(key, _json.dumps(state, ensure_ascii=False))
            return out
        cap = config_mod.meetings_cap(ctx.cfg, m)  # 하루 AI 거부권 확인 수 (0 = 이 시장 AI 확인 꺼짐 → 사지 않음)
        if cap <= 0 or signals.veto_today(ctx.store, m, today) >= cap:
            return out
        if not signals.ai_ready():  # Claude 키가 없으면 신호가 떠도 확인할 수 없어 사지 않는다 (화면에 알린다)
            if ctx.store.get(f"intraday_skip_{m.value}_{today}") != "no_key":
                ctx.store.set(f"intraday_skip_{m.value}_{today}", "no_key")
                ctx.store.log("warn", f"intraday.{m.value}", "Claude API 키가 없어 사는 신호를 확인하지 못해요. 설정 → 2. 연결에서 키를 저장하세요")
            return out
        cost_cap = float(ai.get("monthly_cost_cap_krw") or 0)
        if cost_cap and extras.month_cost_krw(ctx.store) >= cost_cap:
            return out
        try:
            picks = todays_later(ctx, m, today, cap) if ai.get("auto_pick", True) else \
                [{"symbol": s} for s in ctx.cfg["markets"][m.value]["universe"]]
        except Exception as ex:
            ctx.store.log("warn", f"intraday.{m.value}", f"후보 고르기 실패: {ex}")
            return out
        try:
            held = set(ctx.broker.account(m).positions)
        except Exception:
            held = set()
        bought = ctx.store.ordered_today(ctx.broker.name, m.value, today)
        watch = [p["symbol"] for p in picks if p["symbol"] not in (set(state) | held | bought) and not p.get("held")][:30]
        from datetime import timezone as _tz
        ctx.store.set(f"intraday_last_{m.value}", datetime.now(_tz.utc).isoformat(timespec="seconds"))  # 화면의 '마지막 확인'
        ctx.store.set(f"signal_watch_{m.value}", _json.dumps({"at": datetime.now(TZ[m]).strftime("%H:%M"), "n": len(watch)}))
        if not watch:
            ctx.store.set(key, _json.dumps(state, ensure_ascii=False))
            return out
        hits = signals.scan(ctx, m, watch, today)
        for sig in hits[:1]:  # 한 틱에 한 종목만 (거부권 비용·주문을 천천히)
            sym = sig["symbol"]
            name = self._name(ctx, sym)
            now_s = datetime.now(TZ[m]).strftime("%H:%M")
            ctx.store.log("info", f"intraday.{m.value}", f"{name} 사는 신호: {sig['name']} — {sig['why']}. AI 거부권 확인")
            v = signals.veto(ctx, m, sym, name, sig)
            run = signals.save_decision(ctx, m, sym, name, sig, v, today)
            e = {"state": "veto", "at": now_s, "signal": sig["name"], "why": sig["why"], "run_id": run["id"]}
            if v.get("veto"):
                e.update(state="vetoed", note=v.get("reason", ""))
                ctx.store.log("info", f"intraday.{m.value}", f"{name} 사지 않음 (거부권): {v.get('reason', '')}")
            else:
                try:
                    res = buy_one(ctx, m, sym)
                except Exception as ex:
                    res = {"status": "error", "message": str(ex), "qty": 0}
                    ctx.store.log("error", f"intraday.{m.value}", f"{sym} 장중 매수 실패: {ex}")
                e.update(state={"filled": "bought", "submitted": "bought", "pending_approval": "approval"}.get(res["status"], "skipped"),
                         note=res.get("message", ""), qty=res.get("qty", 0))
                out.append(res | {"symbol": sym})
                if res["status"] in ("filled", "submitted", "pending_approval"):
                    head = "🛒 장중 매수" if res["status"] != "pending_approval" else "📝 장중 매수 승인 대기"
                    extras.notify(ctx.store, ctx.cfg, f"{head} {name} {res['qty']}주 — {sig['name']} 신호 · AI 거부권 통과 "
                                  f"(손절 -{sig['stop_pct']}% · 익절 +{sig['take_pct']}%)", "order")
            state[sym] = e
        ctx.store.set(key, _json.dumps(state, ensure_ascii=False))
        return out

    @staticmethod
    def _name(ctx, symbol: str) -> str:
        client = getattr(ctx.data, "client", None)
        try:
            got = client.stocks([symbol]) if client is not None and hasattr(client, "stocks") else []
            return (got[0].get("name") if got else None) or symbol
        except Exception:
            return symbol

    @staticmethod
    def _session_open(ctx, m: Market) -> bool:
        """토스증권 장 운영 정보로 휴장일을 거른다. 조회할 수 없으면 요일 기준(due)을 믿는다."""
        client = getattr(ctx.data, "client", None)
        if client is None or not hasattr(client, "in_regular_session"):
            return True
        try:
            return client.in_regular_session(m)
        except Exception:
            return True

    def _ai_meetings(self, ctx, m: Market, mc: dict) -> None:
        """실행 시각 전에 오늘 회의가 없는 종목부터 하나씩 회의를 연다 (하루 상한까지)."""
        from .agents import meeting

        ai = ctx.cfg["ai"]
        hh, mm = (int(x) for x in mc["run_time"].split(":"))
        t = datetime.now(run_clock(ctx.cfg, m)[0])
        start = t.replace(hour=hh, minute=mm, second=0, microsecond=0) - timedelta(minutes=int(ai["run_before_minutes"]))
        if t.weekday() >= 5 or not (start <= t < start + timedelta(minutes=int(ai["run_before_minutes"]))):
            return
        if any(r["status"] == "running" for r in meeting.live_runs()):
            return
        today = trade_date(m)
        done, cap = config_mod.met_today(ctx.store, m, today), config_mod.meetings_cap(ctx.cfg, m)
        if len(config_mod.met_today(ctx.store, m, today, auto_only=True)) >= cap:
            return
        if ai.get("auto_pick", True):  # 종목을 정하지 않아도 AI 가 후보를 고른다
            symbols = [p["symbol"] for p in todays_later(ctx, m, today, cap)]
        else:
            symbols = mc["universe"]
        for s in symbols:
            if s not in done:
                ctx.store.log("info", f"ai.{m.value}", f"{s} AI 회의 시작")
                try:
                    meeting.start(s, ctx.data, ctx.store, ctx.cfg, toss=getattr(ctx.data, "client", None)
                                  if getattr(ctx.data, "name", "") == "toss" else None, origin="auto")
                except ValueError as e:  # 월 비용 상한 등
                    ctx.store.log("warn", f"ai.{m.value}", str(e))
                return

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
                self.last_error = ""
            except Exception as e:
                self.last_error = str(e)
            self._stop.wait(self.interval)
