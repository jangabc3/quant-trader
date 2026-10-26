"""시장별 실행 시각에 하루 한 번 엔진을 돌린다. 자동매매가 켜져 있을 때만."""
from __future__ import annotations

import threading
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
        if ctx.store.get("auto_trading", "0") != "1":
            return []
        out = []
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
                out.append(run_market(ctx, m))
            except Exception as e:
                ctx.store.log("error", f"scheduler.{m.value}", f"실행 실패: {e}")
                # 같은 날 계속 재시도하며 주문이 쌓이지 않도록 실패도 실행으로 기록
                ctx.store.mark_run(trade_date(m), ctx.broker.name, m.value)
        return out

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
        for s in mc["universe"]:
            if s not in done:
                ctx.store.log("info", f"ai.{m.value}", f"{s} AI 회의 시작")
                meeting.start(s, ctx.data, ctx.store, ctx.cfg, toss=getattr(ctx.data, "client", None)
                              if getattr(ctx.data, "name", "") == "toss" else None)
                return

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
                self.last_error = ""
            except Exception as e:
                self.last_error = str(e)
            self._stop.wait(self.interval)
