"""전략 비교: 같은 AI 회의 결과로 매매 기간만 다른 가상 계좌 여러 개를 나란히 돌린다 (AI 비용 추가 없음).

계좌 이름: cmp-auto, cmp-long, cmp-swing, cmp-short. 실제 계좌와 별개로 내 PC 안에서만 체결된다.
"""
from __future__ import annotations

import copy

from . import principles
from .broker import PaperBroker
from .engine import Context, check_exits, run_market, snapshot_equity, trade_date
from .models import Market

MODES = ["auto", "long", "swing", "short"]


def contexts(ctx) -> list[tuple[str, Context]]:
    out = []
    for mode in (ctx.cfg.get("compare") or {}).get("modes", MODES):
        cfg = copy.deepcopy(ctx.cfg)
        cfg["ai"]["holding"] = mode
        cfg["ai"]["require_approval"] = False
        broker = PaperBroker(ctx.store, cfg["paper"]["initial_cash"], ctx.data.price, account_id=f"cmp-{mode}")
        out.append((mode, Context(cfg, ctx.store, ctx.data, broker)))
    return out


def _fit_plans(c: Context, market: Market, mode: str) -> None:
    """회의 계획(실제 계좌 기준)을 이 계좌의 매매 기간에 맞게 손절·익절·기한을 다시 잡는다."""
    today = trade_date(market)
    for p in c.store.active_plans(c.broker.name, market.value):
        if not p["created_at"].startswith(today[:10]) and p.get("horizon") is not None:
            continue
        d = c.store.latest_decision(p["symbol"], today) or {}
        f = principles.adjust_for_holding(d.get("final") or {}, mode) or {}
        sl, tp = float(f.get("stop_loss_pct") or 0), float(f.get("take_profit_pct") or 0)
        if not sl or not tp:
            continue
        e = p["entry"]
        h = int(f.get("horizon_days") or 0)
        c.store.save_plan(c.broker.name, market.value, p["symbol"], p.get("run_id"), e, e * (1 - sl / 100), e * (1 + tp / 100),
                          h if 0 < h <= 10 else None)


def run(ctx, market: Market) -> list[dict]:
    """정기 실행 때 (실제 계좌 다음에) 비교 계좌도 같은 회의 결과로 실행."""
    out = []
    for mode, c in contexts(ctx):
        try:
            r = run_market(c, market)
            _fit_plans(c, market, mode)
            out.append({"mode": mode, "orders": len(r.get("orders", []))})
        except Exception as e:
            ctx.store.log("warn", f"compare.{market.value}", f"비교 계좌 {mode} 실행 실패: {e}")
    return out


def watch(ctx, market: Market) -> None:
    for _, c in contexts(ctx):
        try:
            check_exits(c, market)
        except Exception:
            pass


def snapshot(ctx, market: Market) -> None:
    for _, c in contexts(ctx):
        try:
            snapshot_equity(c, market)
        except Exception:
            pass


def summary(ctx, market: Market) -> list[dict]:
    from .extras import performance
    rows = []
    for mode, c in contexts(ctx):
        a = c.broker.account(market)
        base = float(c.cfg["paper"]["initial_cash"][market.value])
        perf = performance(c.store, c.broker.name, market.value, {s: p.last_price for s, p in a.positions.items()})["summary"]
        hist = c.store.equity_history(c.broker.name, market.value)
        rows.append({"mode": mode, "name": principles.HOLDING[mode]["name"], "equity": a.equity, "return": a.equity / base - 1 if base else None,
                     "positions": len(a.positions), "trades": perf["count"], "win_rate": perf["win_rate"], "mdd": perf["mdd"],
                     "history": [{"time": h["trade_date"], "value": h["equity"] / base - 1} for h in hist]})
    return rows
