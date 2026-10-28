"""국내 전체 종목 훑기 (하루 한 번, 뒤에서).

회의 후보를 거래대금·급상승 순위 등에서 모은 약 120종목에서만 고르면, 국내 2,500여 종목 중 대부분을 보지 못한다.
그래서 하루 한 번 코스피·코스닥 전체 보통주의 일봉을 받아 멀티팩터 재료(screener.factors)를 계산해 둔다.
- 종목마다 일봉을 한 번씩 받아야 해서 몇 분 걸린다 (토스 차트 호출 간격 0.06초). 그동안 화면·매매는 그대로 돈다.
- 다 훑으면 오늘 회의 후보를 다시 고른다 (이미 고른·회의한 종목은 그대로 두고 새 후보를 뒤에 붙인다).
- 숫자 규칙(200일선·모멘텀·과열·변동성·거래가 너무 적은 종목)은 screener.candidates 가 똑같이 건다.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

from .models import Market

KR_BOARDS = ("KOSPI", "KOSDAQ")
_running: set[str] = set()
_lock = threading.Lock()


def key(market: Market) -> str:
    return f"scan_{market.value}"


def universe(rows: list[dict]) -> list[str]:
    """종목 목록에서 국내 보통주만 (ETF·ETN·리츠·스팩·우선주는 뺀다)."""
    out = []
    for r in rows:
        name = r.get("name") or ""
        if r.get("market") not in KR_BOARDS or (r.get("type") or "STOCK") != "STOCK":
            continue
        if "스팩" in name or name.endswith(("우", "우B", "우C", "(전환)")):
            continue
        out.append(r["symbol"])
    return out


def load(store, market: Market) -> dict | None:
    try:
        return json.loads(store.get(key(market)) or "null")
    except ValueError:
        return None


def usable(store, market: Market, today: str) -> dict | None:
    """오늘(또는 어제 저녁) 끝까지 훑은 결과. 없으면 None."""
    s = load(store, market)
    return s if s and s.get("finished") and _age_days(s) <= 1 else None


def _age_days(s: dict) -> float:
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(s["at"])).total_seconds() / 86400
    except (KeyError, ValueError):
        return 99


def run(ctx, market: Market, today: str, symbols: list[str], save_every: int = 100) -> dict:
    """모든 종목의 일봉을 받아 재료를 계산해 저장한다. 끝나면 오늘 후보를 다시 고른다."""
    from .screener import factors
    rows, no_data = [], 0
    state = {"date": today, "total": len(symbols), "done": 0, "finished": False, "rows": [], "no_data": 0,
             "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    for i, s in enumerate(symbols, 1):
        try:
            bars = ctx.data.bars(market, s, 300)
            if len(bars) < 60:
                no_data += 1
            else:
                rows.append({"symbol": s, "f": {k: float(v) for k, v in factors(bars).items()}})
        except Exception:
            no_data += 1
        if i % save_every == 0:  # 화면에 '훑는 중 1,200/2,500' 을 보여 주려고 (중간 결과는 아직 쓰지 않는다)
            prev = load(ctx.store, market) or {}
            ctx.store.set(key(market), json.dumps(prev | {"progress": {"date": today, "done": i, "total": len(symbols)}}))
    state.update(rows=rows, no_data=no_data, done=len(symbols), finished=True, at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    ctx.store.set(key(market), json.dumps(state, ensure_ascii=False))
    ctx.store.log("info", f"picks.{market.value}", f"국내 전체 {len(symbols):,}종목을 훑었어요 (자료 부족 {no_data}종목). 오늘 후보를 다시 고릅니다")
    refresh_picks(ctx, market, today)
    return state


def refresh_picks(ctx, market: Market, today: str) -> list[dict]:
    """전체를 훑은 결과로 오늘 후보를 다시 고른다. 이미 있던 후보(회의한 종목 포함)는 앞에 그대로 둔다."""
    from . import config as config_mod
    from .screener import CANDIDATES_SHOWN, candidates, stats_key
    pk = f"picks_{market.value}_{today}"
    old = json.loads(ctx.store.get(pk) or "[]")
    st: dict = {}
    new = candidates(ctx, market, max(config_mod.meetings_cap(ctx.cfg, market), CANDIDATES_SHOWN), st)
    have = {p["symbol"] for p in old}
    merged = old + [p for p in new if p["symbol"] not in have]
    ctx.store.set(pk, json.dumps(merged, ensure_ascii=False))
    ctx.store.set(stats_key(market, today), json.dumps(st))
    return merged


def maybe_start(ctx, market: Market, today: str) -> bool:
    """오늘 아직 안 훑었으면 뒤에서 시작한다 (토스 시세일 때만, 한 번에 하나). 시작했으면 True."""
    if market != Market.KR or getattr(ctx.data, "name", "") != "toss":
        return False
    s = load(ctx.store, market) or {}
    if s.get("finished") and s.get("date") == today:
        return False
    with _lock:
        if market.value in _running:
            return False
        _running.add(market.value)

    def work():
        try:
            from . import stockdata
            from . import config as config_mod
            from .engine import build_context
            c = build_context()  # 이 스레드 전용 연결 (DB·토스)
            syms = universe(stockdata.master(c.data.client, config_mod.home()))
            if syms:
                run(c, market, today, syms)
        except Exception as e:
            try:
                ctx.store.log("warn", f"picks.{market.value}", f"전체 종목 훑기 실패: {e}")
            except Exception:
                pass
        finally:
            with _lock:
                _running.discard(market.value)
    threading.Thread(target=work, name=f"scan-{market.value}", daemon=True).start()
    return True


def status(store, market: Market, today: str) -> dict | None:
    """화면용: 다 훑었는지, 훑는 중이면 몇 종목째인지."""
    s = load(store, market)
    if not s:
        return {"running": market.value in _running, "done": 0, "total": 0} if market.value in _running else None
    p = s.get("progress") or {}
    if market.value in _running and p.get("date") == today:
        return {"running": True, "done": p.get("done", 0), "total": p.get("total", 0)}
    return {"running": market.value in _running, "finished": bool(s.get("finished")), "date": s.get("date"),
            "total": s.get("total", 0), "no_data": s.get("no_data", 0)}
