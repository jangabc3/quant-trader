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
            if not mc.get("enabled") or not due(m, mc["run_time"]):
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

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
                self.last_error = ""
            except Exception as e:
                self.last_error = str(e)
            self._stop.wait(self.interval)
