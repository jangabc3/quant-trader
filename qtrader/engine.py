"""실매매 엔진: 데이터 -> 전략 목표 비중 -> 리스크 -> 주문."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from . import config as config_mod
from . import strategies, vault
from .broker import Broker, KISBroker, PaperBroker, TossBroker
from .data import CSVData, DataSource, KISData, TossData, panel
from .kis import KIS
from .toss import Toss
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
    keys = vault.get_kis(env)
    if not keys or not cfg["kis"].get("account"):
        return None
    return KIS(env, keys[0], keys[1], cfg["kis"]["account"], config_mod.home())


def toss_client(cfg: dict) -> Toss | None:
    keys = vault.get_toss()
    if not keys:
        return None
    return Toss(keys[0], keys[1], config_mod.home(), cfg.get("toss", {}).get("account_seq"))


def pick_data(cfg: dict, store: Store) -> DataSource:
    want = cfg.get("data_source", "auto")
    if want in ("auto", "toss"):
        t = toss_client(cfg)
        if t:
            return TossData(t, store)
        if want == "toss":
            raise RuntimeError("토스증권 키를 먼저 등록하세요")
    if want in ("auto", "kis"):
        k = kis_client(cfg, "demo") or kis_client(cfg, "real")
        if k:
            return KISData(k, store)
        if want == "kis":
            raise RuntimeError("KIS 키를 먼저 등록하세요")
    return CSVData(config_mod.home() / "data")


def build_context(cfg: dict | None = None, store: Store | None = None) -> Context:
    cfg = cfg or config_mod.load()
    store = store or Store(config_mod.home() / "qtrader.db")
    mode = cfg["mode"]
    if mode in config_mod.LIVE_MODES and not cfg.get("live_confirmed"):
        raise RuntimeError("실전 모드는 설정에서 실전 주문 확인(live_confirmed)을 켜야 합니다")
    if mode == "paper":
        data = pick_data(cfg, store)
        broker: Broker = PaperBroker(store, cfg["paper"]["initial_cash"], data.price)
        return Context(cfg, store, data, broker)
    if mode == "toss_real":
        t = toss_client(cfg)
        if t is None:
            raise RuntimeError("토스증권 Client ID/Secret 을 먼저 등록하세요")
        return Context(cfg, store, TossData(t, store), TossBroker(t, cfg["toss"].get("us_loc", True)))
    env = "real" if mode == "kis_real" else "demo"
    client = kis_client(cfg, env)
    if client is None:
        raise RuntimeError(f"{env} KIS 키와 계좌번호를 먼저 등록하세요")
    return Context(cfg, store, KISData(client, store), KISBroker(client))


def ai_targets(store: Store, cfg: dict, universe: list[str], today: str) -> tuple[dict, set, list]:
    """오늘 AI 회의 결정으로 목표 비중을 만든다. 결정이 없거나 Hold·확신 부족이면 보유를 유지(keep)."""
    ai = cfg["ai"]
    mult = ai["rating_weights"]
    base = 1.0 / max(len(universe), 1)
    target, keep, notes = {}, set(), []
    for s in universe:
        d = store.latest_decision(s, today)
        f = (d or {}).get("final") or {}
        rating, conf = f.get("rating"), float(f.get("confidence") or 0)
        m = mult.get(rating) if rating else None
        if not d or m is None or conf < ai.get("min_confidence", 0.5):
            keep.add(s)
            why = "회의 결과 없음" if not d else ("보유 유지" if m is None else f"확신 부족 {conf:.0%}")
        else:
            target[s] = base * float(m)
            why = f"{rating} {conf:.0%}"
        notes.append({"symbol": s, "rating": rating, "confidence": conf, "action": why})
    return target, keep, notes


def plan_orders(target: dict[str, float], prices: dict[str, float], holdings: dict[str, int],
                capital: float, market: Market, lim: RiskLimits, strategy: str,
                limit_offset: float = 0.0, keep: frozenset | set = frozenset()) -> list[OrderRequest]:
    """목표 비중과 현재 보유의 차이를 주문으로. 매도를 먼저 둔다. keep 에 든 종목은 건드리지 않는다."""
    sells, buys = [], []
    for s in sorted(set(target) | set(holdings)):
        if s in keep:
            continue
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

    use_ai = mc["strategy"] == "ai"
    strat = None if use_ai else strategies.build(mc["strategy"], mc.get("params"))
    strat_name = "ai" if use_ai else strat.name
    errors: list[str] = []
    p = panel(ctx.data, market, mc["universe"], cfg["history_days"], errors)
    for e in errors:
        store.log("warn", src, f"데이터 실패 {e}")
    if not p:
        store.log("error", src, "쓸 수 있는 데이터가 없습니다")
        return {"error": "데이터 없음", "data_errors": errors}

    lim = RiskLimits.from_config(cfg, market)
    keep: set[str] = set()
    ai_notes: list[dict] = []
    if use_ai:
        raw, keep, ai_notes = ai_targets(store, cfg, list(p), today)
    else:
        raw = strat.latest(p)
    target = cap_weights(raw, lim)
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

    orders = plan_orders(target, prices, holdings, capital, market, lim, strat_name, mc.get("limit_offset", 0.0),
                         keep)
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
        "trade_date": today, "account": acct_id, "strategy": strat_name, "ai": ai_notes, "capital": capital,
        "target": {k: round(v, 4) for k, v in target.items() if v > 0},
        "orders": results, "blocked": [{"symbol": o.symbol, "side": o.side.value, "reason": w}
                                       for o, w in checked.blocked],
        "kill_switch": kill, "data_errors": errors, "dry_run": dry_run,
    }
