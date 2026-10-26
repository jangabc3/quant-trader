"""실매매 엔진: 데이터 -> 전략 목표 비중 -> 리스크 -> 주문."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from . import config as config_mod
from . import strategies, vault
from .broker import Broker, KISBroker, PaperBroker
from .data import CSVData, DataSource, KISData, panel
from .kis import KIS
from .models import Market, OrderRequest, Side
from .risk import RiskLimits, cap_weights, check_orders, clamp_qty, loss_breached
from .storage import Store

TZ = {Market.KR: ZoneInfo("Asia/Seoul"), Market.US: ZoneInfo("America/New_York")}


def trade_date(market: Market, now: datetime | None = None) -> str:
    return (now or datetime.now(TZ[market])).astimezone(TZ[market]).strftime("%Y-%m-%d")


@dataclass
class Context:
    cfg: dict
    store: Store
    data: DataSource
    broker: Broker

    @property
    def kill(self) -> bool:
        return self.store.get("kill_switch", "0") == "1"


def kis_client(cfg: dict, env: str) -> KIS | None:
    keys = vault.get_kis(env) if vault.has_kis(env) else None
    if not keys or not cfg["kis"].get("account"):
        return None
    return KIS(env, keys[0], keys[1], cfg["kis"]["account"], config_mod.home())


def build_context(cfg: dict | None = None, store: Store | None = None) -> Context:
    cfg = cfg or config_mod.load()
    store = store or Store(config_mod.home() / "qtrader.db")
    mode = cfg["mode"]
    if mode == "paper":
        # 가상매매도 시세는 KIS(모의 키 우선)로 받고, 키가 없으면 CSV 로
        client = kis_client(cfg, "demo") or kis_client(cfg, "real")
        data: DataSource = KISData(client, store) if client else CSVData(config_mod.home() / "data")
        broker: Broker = PaperBroker(store, cfg["paper"]["initial_cash"], data.price)
        return Context(cfg, store, data, broker)
    env = "real" if mode == "kis_real" else "demo"
    client = kis_client(cfg, env)
    if client is None:
        raise RuntimeError(f"{env} KIS 키와 계좌번호를 먼저 등록하세요")
    if env == "real" and not cfg.get("live_confirmed"):
        raise RuntimeError("실전 모드는 설정에서 실전 주문 확인(live_confirmed)을 켜야 합니다")
    return Context(cfg, store, KISData(client, store), KISBroker(client))


def plan_orders(target: dict[str, float], prices: dict[str, float], holdings: dict[str, int],
                capital: float, market: Market, lim: RiskLimits, strategy: str,
                limit_offset: float = 0.0) -> list[OrderRequest]:
    """목표 비중과 현재 보유의 차이를 주문으로. 매도를 먼저 둔다."""
    sells, buys = [], []
    for s in sorted(set(target) | set(holdings)):
        px = prices.get(s)
        if not px:
            continue
        want = math.floor(capital * target.get(s, 0.0) / px)
        diff = want - holdings.get(s, 0)
        if diff == 0:
            continue
        side = Side.BUY if diff > 0 else Side.SELL
        qty = clamp_qty(abs(diff), px, lim)
        if qty == 0:
            continue
        if market == Market.US:
            # 미국은 지정가만 쓴다. 체결이 잘 되도록 약간 불리한 쪽으로 건다
            order_px = round(px * (1 + limit_offset) if side == Side.BUY else px * (1 - limit_offset), 2)
        else:
            order_px = px
        req = OrderRequest(market, s, side, qty, order_px, strategy,
                           f"목표 {target.get(s, 0):.1%} 보유 {holdings.get(s, 0)} -> {want}")
        (buys if side == Side.BUY else sells).append(req)
    return sells + buys


def run_market(ctx: Context, market: Market, dry_run: bool = False, force: bool = False) -> dict:
    cfg, store = ctx.cfg, ctx.store
    mc = cfg["markets"][market.value]
    acct_id = ctx.broker.name
    today = trade_date(market)
    src = f"engine.{market.value}"

    if not force and not dry_run and store.ran(today, acct_id, market.value):
        return {"skipped": "오늘 이미 실행했습니다"}

    strat = strategies.build(mc["strategy"], mc.get("params"))
    errors: list[str] = []
    p = panel(ctx.data, market, mc["universe"], cfg["history_days"], errors)
    for e in errors:
        store.log("warn", src, f"데이터 실패 {e}")
    if not p:
        store.log("error", src, "쓸 수 있는 데이터가 없습니다")
        return {"error": "데이터 없음", "data_errors": errors}

    lim = RiskLimits.from_config(cfg, market)
    target = cap_weights(strat.latest(p), lim)
    prices = {s: float(df["close"].iloc[-1]) for s, df in p.items()}

    acct = ctx.broker.account(market)
    universe = set(mc["universe"])
    holdings = {s: pos.qty for s, pos in acct.positions.items() if s in universe}
    for s, pos in acct.positions.items():
        if s in universe and pos.last_price:
            prices[s] = pos.last_price
    managed = acct.cash + sum(pos.value for s, pos in acct.positions.items() if s in universe)
    capital = min(float(mc["budget"]), managed)

    start_eq = store.day_start_equity(acct_id, market.value, today)
    kill = ctx.kill
    if loss_breached(start_eq, managed, lim) and not kill:
        store.set("kill_switch", "1")
        kill = True
        store.log("error", src, f"일일 손실 한도 도달 ({managed / start_eq - 1:.2%}). 킬 스위치를 켰습니다")

    orders = plan_orders(target, prices, holdings, capital, market, lim, strat.name, mc.get("limit_offset", 0.0))
    checked = check_orders(orders, lim, universe, kill, store.ordered_today(acct_id, market.value, today),
                           acct.cash)
    for o, why in checked.blocked:
        store.log("warn", src, f"차단 {o.side.value} {o.symbol} x{o.qty}: {why}")

    results = []
    for o in checked.allowed:
        if dry_run:
            status, res_msg, oid = "dry_run", "모의 실행(주문 안 함)", ""
        else:
            r = ctx.broker.order(o)
            status = ("filled" if r.filled_qty else "submitted") if r.ok else "rejected"
            res_msg, oid = r.message, r.broker_order_id
        store.add_order(trade_date=today, account=acct_id, market=market.value, symbol=o.symbol,
                        side=o.side.value, qty=o.qty, price=o.price, status=status, broker_order_id=oid,
                        strategy=o.strategy, message=f"{o.reason} | {res_msg}")
        store.log("info" if status != "rejected" else "error", src,
                  f"{status} {o.side.value} {o.symbol} x{o.qty} @ {o.price:,.2f} {res_msg}")
        results.append({"symbol": o.symbol, "side": o.side.value, "qty": o.qty, "price": o.price,
                        "status": status, "message": res_msg})

    if not dry_run:
        after = ctx.broker.account(market)
        store.add_equity(today, acct_id, market.value, after.equity, after.cash)
        store.mark_run(today, acct_id, market.value)
    store.log("info", src, f"실행 완료: 주문 {len(results)}건, 차단 {len(checked.blocked)}건"
              + (" (dry-run)" if dry_run else ""))
    return {
        "trade_date": today, "account": acct_id, "strategy": strat.name, "capital": capital,
        "target": {k: round(v, 4) for k, v in target.items() if v > 0},
        "orders": results, "blocked": [{"symbol": o.symbol, "side": o.side.value, "reason": w}
                                       for o, w in checked.blocked],
        "kill_switch": kill, "data_errors": errors, "dry_run": dry_run,
    }
