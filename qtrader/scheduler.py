"""시장별 실행 시각에 하루 한 번 엔진을 돌린다. 자동매매가 켜져 있을 때만."""
from __future__ import annotations

import json
import threading
import time
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


def due(market: Market, run_time: str, now: datetime | None = None) -> bool:
    """오늘 실행 시각이 지났고 아직 장중이면 True. 주말은 False. (휴장일은 증권사 거부로 걸러진다)"""
    t = (now or datetime.now(TZ[market])).astimezone(TZ[market])
    return t.weekday() < 5 and parse_hm(run_time) <= t.time() < SESSIONS[market][1]


def next_run(market: Market, run_time: str, now: datetime | None = None) -> datetime:
    t = (now or datetime.now(TZ[market])).astimezone(TZ[market])
    cand = t.replace(hour=parse_hm(run_time).hour, minute=parse_hm(run_time).minute, second=0, microsecond=0)
    if cand <= t:
        cand += timedelta(days=1)
    while cand.weekday() >= 5:
        cand += timedelta(days=1)
    return cand


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
        if time.monotonic() - getattr(self, "_last_snap", 0) > 600:
            self._last_snap = time.monotonic()
            from .engine import snapshot_equity
            for m in (Market.KR, Market.US):
                if cfg["markets"][m.value].get("enabled") and after_close(m):
                    try:
                        snapshot_equity(ctx, m)
                    except Exception:
                        pass
        auto = ctx.store.get("auto_trading", "0") == "1"
        self._heartbeat(ctx, cfg, auto)
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
            for m in (Market.KR, Market.US):
                if not cfg["markets"][m.value].get("enabled"):
                    continue
                try:
                    if is_open(m):
                        was_killed = ctx.kill
                        if check_loss(ctx, m) and not was_killed:
                            extras.notify(ctx.store, cfg, f"⛔ {m.value} 하루 손실 한도를 넘어 신규 매수를 멈췄습니다", "risk")
                        for x in check_exits(ctx, m):
                            extras.notify(ctx.store, cfg, f"{'🛑 손절' if x['reason'] == 'stop' else '🎯 익절'} "
                                                          f"{x['symbol']} @ {x['price']:,} ({x['status']})", "exit")
                        if cfg["markets"][m.value].get("strategy") == "ai":
                            from . import live
                            live.run_rechecks(ctx, m)
                            self._new_candidates(ctx, m)
                    elif after_close(m) and snapshot_equity(ctx, m):
                        today = trade_date(m)
                        rep = extras.daily_report(ctx.store, ctx.broker.name, m.value, today)
                        ctx.store.set(f"report_{m.value}", json.dumps(rep, ensure_ascii=False, default=str))
                        if (cfg.get("notify") or {}).get("daily_report", True):
                            extras.notify(ctx.store, cfg, rep["text"], "report")
                except Exception as e:
                    ctx.store.log("error", f"exit.{m.value}", f"장중 점검 실패: {e}")
        for m in (Market.KR, Market.US):
            mc = cfg["markets"].get(m.value, {})
            if mc.get("enabled") and mc.get("strategy") == "ai":
                self._ai_meetings(ctx, m, mc)
            if not mc.get("enabled") or not due(m, mc["run_time"]):
                continue
            if not self._session_open(ctx, m):
                ctx.store.log("info", f"scheduler.{m.value}", "휴장일이라 실행하지 않습니다")
                ctx.store.mark_run(trade_date(m), ctx.broker.name, m.value)
                continue
            if ctx.store.ran(trade_date(m), ctx.broker.name, m.value):
                continue
            try:
                res = run_market(ctx, m)
                out.append(res)
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
        """20초마다 살아 있다고 적어 둔다. 장중에 10분 넘게 비어 있었으면 (PC·프로그램이 꺼졌던 것) 알리고 바로 다시 점검한다."""
        from datetime import timezone as _tz
        from . import extras
        now = datetime.now(_tz.utc)
        last = ctx.store.get("heartbeat")
        ctx.store.set("heartbeat", now.isoformat(timespec="seconds"))
        if not last or not auto:
            return
        prev = datetime.fromisoformat(last)
        gap = (now - prev).total_seconds()
        if gap < 600:
            return
        hit = [m for m in (Market.KR, Market.US) if cfg["markets"][m.value].get("enabled") and (is_open(m, prev) or is_open(m, now))]
        if not hit:
            return
        loc = lambda t: t.astimezone().strftime("%m-%d %H:%M")
        extras.notify(ctx.store, cfg, f"⚠️ 프로그램이 {int(gap // 60)}분 동안 꺼져 있었어요 ({loc(prev)} ~ {loc(now)}). "
                                      f"그동안 손절·익절 감시가 멈췄습니다. 지금 바로 다시 확인합니다.", "live")
        self._last_exit = 0.0  # 곧바로 체결 확인·손절 점검

    def _new_candidates(self, ctx, m: Market) -> None:
        """15분마다 숫자 규칙을 다시 돌려, 새로 통과한 종목을 오늘 회의 후보에 올린다 (매수는 정기 실행 때만)."""
        import json as _json
        from . import live
        key = f"_cand_{m.value}"
        if time.monotonic() - getattr(self, key, 0) < 900:
            return
        setattr(self, key, time.monotonic())
        today = trade_date(m)
        pk = f"picks_{m.value}_{today}"
        picks = _json.loads(ctx.store.get(pk) or "[]")
        have = {p["symbol"] for p in picks}
        fresh = [c for c in live.next_candidates(ctx, m) if not c["missing"] and c["symbol"] not in have]
        if fresh and len(picks) < int(ctx.cfg["ai"]["max_symbols_per_day"]):
            add = fresh[: int(ctx.cfg["ai"]["max_symbols_per_day"]) - len(picks)]
            picks += [{"symbol": c["symbol"], "score": c["score"], "reason": "장중 새로 규칙 통과", "held": False, "fails": []} for c in add]
            ctx.store.set(pk, _json.dumps(picks, ensure_ascii=False))
            ctx.store.log("info", f"live.{m.value}", "새 후보: " + ", ".join(c["symbol"] for c in add) + " (다음 정기 회의·실행 때 검토)")

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
        t = datetime.now(TZ[m])
        start = t.replace(hour=hh, minute=mm, second=0, microsecond=0) - timedelta(minutes=int(ai["run_before_minutes"]))
        if t.weekday() >= 5 or not (start <= t < start + timedelta(minutes=int(ai["run_before_minutes"]))):
            return
        if any(r["status"] == "running" for r in meeting.live_runs()):
            return
        today = trade_date(m)
        done = {r["symbol"] for r in ctx.store.ai_runs(200) if r["trade_date"] == today}
        if len(done) >= int(ai["max_symbols_per_day"]):
            return
        if ai.get("auto_pick", True):  # 종목을 정하지 않아도 AI 가 후보를 고른다
            from .screener import todays
            symbols = [p["symbol"] for p in todays(ctx, m, today, int(ai["max_symbols_per_day"]))]
        else:
            symbols = mc["universe"]
        for s in symbols:
            if s not in done:
                ctx.store.log("info", f"ai.{m.value}", f"{s} AI 회의 시작")
                try:
                    meeting.start(s, ctx.data, ctx.store, ctx.cfg, toss=getattr(ctx.data, "client", None)
                                  if getattr(ctx.data, "name", "") == "toss" else None)
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
