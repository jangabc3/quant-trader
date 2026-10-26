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


def ai_targets(store: Store, cfg: dict, panel_: dict, today: str, account: str = "", market: str = "KR",
               warnings_for=None) -> tuple[dict, set, list]:
    """오늘 AI 회의 결정으로 목표 비중을 만든다.
    - 결정이 없거나 Hold·확신 부족: 보유 유지(keep)
    - 매수 계열인데 체크리스트(뇌동매매 방지 규칙)를 하나라도 못 넘으면: 신규 매수 없이 보유 유지
    - 매도 계열: 위험을 줄이는 쪽이라 그대로 반영"""
    from . import principles
    ai = cfg["ai"]
    mult = ai["rating_weights"]
    rules = ai.get("rules") or {}
    cooldown = int(rules.get("cooldown_days", principles.DEFAULT_RULES["cooldown_days"]))
    universe = list(panel_)
    base = 1.0 / max(len(universe), 1)
    new_buys = store.new_buys_today(account, market, today) if account else 0
    target, keep, notes = {}, set(), []
    for s in universe:
        d = store.latest_decision(s, today)
        f = (d or {}).get("final") or {}
        rating, conf = f.get("rating"), float(f.get("confidence") or 0)
        m = mult.get(rating) if rating else None
        note = {"symbol": s, "rating": rating, "confidence": conf, "run_id": (d or {}).get("id"),
                "headline": f.get("headline", ""), "checklist": None}
        if not d or m is None or conf < ai.get("min_confidence", 0.5):
            keep.add(s)
            note["action"] = "회의 결과 없음" if not d else ("보유 유지" if m is None else f"확신 부족 {conf:.0%}")
        elif rating in ("Buy", "Overweight"):
            check = d.get("check") if isinstance(d.get("check"), dict) else None
            warns = warnings_for(s) if warnings_for else []
            stopped = store.recent_stop(account, market, s, cooldown) if account else False
            cl = principles.checklist(f, check, panel_[s], warns, rules, new_buys, stopped)
            note["checklist"] = cl
            if cl["passed"]:
                target[s] = base * float(m) * min(1.0, cl["size"]["weight_factor"] * 4)
                note["action"] = f"{rating} {conf:.0%} · 체크리스트 통과"
                new_buys += 1
            else:
                keep.add(s)
                failed = [r["name"] for r in cl["rules"] if not r["ok"]]
                note["action"] = "매수 보류(규칙 미통과): " + ", ".join(failed)
        else:
            target[s] = base * float(m)
            note["action"] = f"{rating} {conf:.0%} · 비중 축소"
        notes.append(note)
    return target, keep, notes


def order_reason(note: dict, plan: dict | None, base_reason: str) -> str:
    """주문 근거 문장: 왜 사는지/파는지 사람이 읽을 수 있게."""
    parts = [f"AI 회의 {note.get('rating')} (확신도 {note.get('confidence', 0):.0%})"]
    if note.get("headline"):
        parts.append(note["headline"])
    cl = note.get("checklist")
    if cl:
        ok = sum(1 for r in cl["rules"] if r["ok"])
        parts.append(f"체크리스트 {ok}/{len(cl['rules'])} 통과")
    if plan:
        parts.append(f"손절 {plan['stop']:,} (-{plan['stop_pct']}%) · 익절 {plan['take']:,} (+{plan['take_pct']}%)")
    return " | ".join(parts + [base_reason])


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


def remember_plan(store: Store, account: str, market: str, o: OrderRequest, today: str) -> None:
    """AI 매수가 나가면 그 회의의 손절·익절 가격을 포지션 계획으로 저장한다. 매도면 계획을 닫는다."""
    if o.side == Side.SELL:
        store.close_plan(account, market, o.symbol, "sold")
        return
    d = store.latest_decision(o.symbol, today) or {}
    plan = d.get("plan")
    if plan:
        store.save_plan(account, market, o.symbol, d.get("id"), plan["entry"], plan["stop"], plan["take"])


def check_exits(ctx: Context, market: Market) -> list[dict]:
    """손절가·익절가에 닿은 AI 포지션을 판다. 장중에 스케줄러가 몇 분마다 부른다."""
    store, out = ctx.store, []
    plans = store.active_plans(ctx.broker.name, market.value)
    if not plans:
        return out
    acct = ctx.broker.account(market)
    today = trade_date(market)
    for pl in plans:
        pos = acct.positions.get(pl["symbol"])
        if not pos or pos.qty <= 0:
            store.close_plan(ctx.broker.name, market.value, pl["symbol"], "no_position")
            continue
        px = pos.last_price or ctx.data.price(market, pl["symbol"])
        why = "stop" if px <= pl["stop"] else "take" if px >= pl["take"] else None
        if not why:
            continue
        # 국내는 시장가, 미국은 지정가만 되므로 현재가보다 0.3% 낮게
        price = round(px * 0.997, 2) if market == Market.US else 0
        label = "손절" if why == "stop" else "익절"
        req = OrderRequest(market, pl["symbol"], Side.SELL, pos.qty, price, "ai-exit",
                           f"{label}: 현재가 {px:,} / 손절가 {pl['stop']:,} / 익절가 {pl['take']:,}")
        r = ctx.broker.order(req)
        status = ("filled" if r.filled_qty else "submitted") if r.ok else "rejected"
        store.add_order(trade_date=today, account=ctx.broker.name, market=market.value, symbol=req.symbol, side="sell",
                        qty=req.qty, price=req.price or px, status=status, broker_order_id=r.broker_order_id,
                        strategy="ai-exit", message=f"{req.reason} | {r.message}")
        if r.ok:
            store.close_plan(ctx.broker.name, market.value, pl["symbol"], why)
        store.log("warn" if why == "stop" else "info", "exit", f"{req.reason} -> {status} {r.message}")
        out.append({"symbol": req.symbol, "reason": why, "status": status, "price": px})
    return out


def execute_approval(ctx: Context, aid: int) -> dict:
    """승인된 주문을 다시 리스크 확인한 뒤 낸다 (킬 스위치·당일 중복·현금)."""
    store = ctx.store
    rows = [r for r in store.approvals("pending", 500) if r["id"] == aid]
    if not rows:
        raise ValueError("대기 중인 주문이 아닙니다")
    a = rows[0]
    market = Market(a["market"])
    if a["account"] != ctx.broker.name:
        raise ValueError(f"다른 계좌({a['account']})의 주문입니다. 지금 모드는 {ctx.broker.name}")
    if a["trade_date"] != trade_date(market):
        store.decide_approval(aid, "expired", "다른 날 만든 주문")
        raise ValueError("오늘 만든 주문만 승인할 수 있습니다")
    req = OrderRequest(market, a["symbol"], Side(a["side"]), int(a["qty"]), float(a["price"]), a["strategy"] or "",
                       a["reason"] or "")
    lim = RiskLimits.from_config(ctx.cfg, market)
    acct = ctx.broker.account(market)
    chk = check_orders([req], lim, set(ctx.cfg["markets"][market.value]["universe"]), ctx.kill,
                       store.ordered_today(ctx.broker.name, market.value, a["trade_date"]), acct.cash)
    if chk.blocked:
        why = chk.blocked[0][1]
        store.decide_approval(aid, "rejected", f"리스크 차단: {why}")
        raise ValueError(f"리스크 관리에서 막혔습니다: {why}")
    r = ctx.broker.order(req)
    status = ("filled" if r.filled_qty else "submitted") if r.ok else "rejected"
    store.add_order(trade_date=a["trade_date"], account=ctx.broker.name, market=market.value, symbol=req.symbol,
                    side=req.side.value, qty=req.qty, price=req.price, status=status, broker_order_id=r.broker_order_id,
                    strategy=req.strategy, message=f"승인 #{aid} | {req.reason} | {r.message}")
    store.decide_approval(aid, "approved", f"{status} {r.message}")
    if r.ok and req.strategy == "ai":
        remember_plan(store, ctx.broker.name, market.value, req, a["trade_date"])
    store.log("info" if r.ok else "error", "approval", f"승인 #{aid} {status} {req.side.value} {req.symbol} x{req.qty} {r.message}")
    return {"id": aid, "status": status, "message": r.message}


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
        tc = getattr(ctx.data, "client", None) if getattr(ctx.data, "name", "") == "toss" else None

        def warns(sym):
            try:
                return tc.warnings(sym) if tc else []
            except Exception:
                return []
        raw, keep, ai_notes = ai_targets(store, cfg, p, today, acct_id, market.value, warns)
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
    if use_ai:
        by = {n["symbol"]: n for n in ai_notes}
        for o in orders:
            n = by.get(o.symbol)
            if n:
                d = store.latest_decision(o.symbol, today) or {}
                o.reason = order_reason(n, d.get("plan") if o.side == Side.BUY else None, o.reason)
    checked = check_orders(orders, lim, universe, kill, store.ordered_today(acct_id, market.value, today),
                           acct.cash)
    for o, why in checked.blocked:
        store.log("warn", src, f"차단 {o.side.value} {o.symbol} x{o.qty}: {why}")

    results = []
    need_ok = use_ai and cfg["ai"].get("require_approval", True) and not dry_run
    if need_ok:
        store.expire_approvals(market.value, today)
        waiting = store.pending_symbols(acct_id, market.value, today)
    for o in checked.allowed:
        if need_ok:
            if o.symbol in waiting:
                continue
            aid = store.add_approval(trade_date=today, account=acct_id, market=market.value, symbol=o.symbol,
                                     side=o.side.value, qty=o.qty, price=o.price, strategy=o.strategy, reason=o.reason)
            store.log("info", src, f"승인 대기 #{aid} {o.side.value} {o.symbol} x{o.qty}")
            results.append({"symbol": o.symbol, "side": o.side.value, "qty": o.qty, "price": o.price,
                            "status": "pending_approval", "message": f"승인 대기 #{aid}"})
            continue
        if dry_run:
            status, res_msg, oid = "dry_run", "모의 실행(주문 안 함)", ""
        else:
            r = ctx.broker.order(o)
            status = ("filled" if r.filled_qty else "submitted") if r.ok else "rejected"
            res_msg, oid = r.message, r.broker_order_id
        store.add_order(trade_date=today, account=acct_id, market=market.value, symbol=o.symbol,
                        side=o.side.value, qty=o.qty, price=o.price, status=status, broker_order_id=oid,
                        strategy=o.strategy, message=f"{o.reason} | {res_msg}")
        if use_ai and status in ("filled", "submitted"):
            remember_plan(store, acct_id, market.value, o, today)
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
