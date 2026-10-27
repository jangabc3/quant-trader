"""실매매 엔진: 데이터 -> 전략 목표 비중 -> 리스크 -> 주문."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
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
               warnings_for=None, market_bars=None) -> tuple[dict, set, list]:
    """오늘 AI 회의 결정으로 목표 비중을 만든다.
    - 결정이 없거나 Hold·확신 부족: 보유 유지(keep)
    - 매수 계열인데 체크리스트(뇌동매매 방지 규칙)를 하나라도 못 넘으면: 신규 매수 없이 보유 유지
    - 매도 계열: 위험을 줄이는 쪽이라 그대로 반영"""
    from . import principles
    ai = cfg["ai"]
    mult = ai["rating_weights"]
    rules = dict(ai.get("rules") or {})
    h = principles.HOLDING.get(ai.get("holding", "long"), principles.HOLDING["long"])
    if h["buys"]:  # 단기일수록 하루에 조금 더 여러 종목을 작게 산다
        rules["max_new_buys_per_day"] = int(rules.get("max_new_buys_per_day", principles.DEFAULT_RULES["max_new_buys_per_day"])) + h["buys"]
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
            cl = principles.checklist(f, check, panel_[s], warns, rules, new_buys, stopped, market_bars)
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


def kr_tick(p: float) -> int:
    """국내 호가 단위로 내림."""
    t = 1 if p < 2000 else 5 if p < 5000 else 10 if p < 20000 else 50 if p < 50000 else 100 if p < 200000 else 500 if p < 500000 else 1000
    return int(p // t * t)


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
        h = int(plan.get("horizon_days") or 0)
        store.save_plan(account, market, o.symbol, d.get("id"), plan["entry"], plan["stop"], plan["take"], h if 0 < h <= 10 else None)


TOSS_DONE = {"FILLED": "filled", "CANCELED": "rejected", "REJECTED": "rejected", "EXPIRED": "rejected",
             "REPLACED": "rejected", "CANCEL_REJECTED": None}


def _plan_after_fill(ctx: Context, o: dict, price: float) -> None:
    """매수가 (일부라도) 체결되면 손절·익절 계획을 만든다. AI 매수는 회의 계획, 직접 매수는 주문 때 정한 가격."""
    import json as _json
    if any(p["symbol"] == o["symbol"] for p in ctx.store.active_plans(ctx.broker.name, o["market"])):
        return
    if o.get("strategy") == "ai":
        req = OrderRequest(Market(o["market"]), o["symbol"], Side.BUY, int(o["qty"]), float(o["price"]), "ai")
        remember_plan(ctx.store, ctx.broker.name, o["market"], req, o["trade_date"])
    elif o.get("strategy") == "manual":
        mp = _json.loads(ctx.store.get(f"manual_plan_{o['id']}") or "null")
        if mp:
            ctx.store.save_plan(ctx.broker.name, o["market"], o["symbol"], "manual", price, mp["stop"], mp["take"])
    try:  # 체결되자마자 토스 서버 보호(OCO)를 건다
        from . import guard
        guard.sync(ctx)
    except Exception as e:
        ctx.store.log("warn", "guard", f"체결 뒤 토스 보호 걸기 실패: {e}")


def sync_orders(ctx: Context) -> list[dict]:
    """토스증권에 접수만 된 주문의 체결 여부를 확인해 기록을 고친다. 체결된 AI 매수는 이때 손절·익절 계획을 만든다."""
    if ctx.broker.name != "toss":
        return []
    out = []
    for o in ctx.store.open_orders(ctx.broker.name):
        try:
            d = ctx.broker.client.order_detail(o["broker_order_id"])
        except Exception as e:
            ctx.store.log("warn", "sync", f"주문 {o['symbol']} 상태 조회 실패: {e}")
            continue
        st = d.get("status", "")
        ex = d.get("execution") or {}
        fq = int(float(ex.get("filledQuantity") or 0))
        ap = float(ex.get("averageFilledPrice") or 0) or float(o["price"])
        new = TOSS_DONE.get(st)
        if not new:
            # 대기 또는 부분 체결: 체결된 만큼은 바로 손절·익절로 보호하고, 나머지는 다음에 다시 본다
            if 0 < fq < o["qty"] and f"부분 체결 {fq}/" not in (o.get("message") or ""):
                ctx.store.update_order(o["id"], "submitted", f"부분 체결 {fq}/{o['qty']}주")
                ctx.store.log("info", "sync", f"{o['symbol']} {o['side']} 부분 체결 {fq}/{o['qty']}주 @ {ap:,.2f}")
                if o["side"] == "buy":
                    _plan_after_fill(ctx, o, ap)
            continue
        if new == "rejected" and fq > 0:  # 일부만 체결된 채로 취소·만료: 체결된 만큼은 체결로 본다
            note = f"부분 체결 {fq}/{o['qty']}주 @ {ap:,.2f} (나머지 {'취소' if st == 'CANCELED' else '만료'})"
            new = "filled"
        else:
            note = {"FILLED": f"체결 {fq or o['qty']}주 @ {ap:,.2f}", "CANCELED": "취소됨", "REJECTED": "거부됨",
                    "EXPIRED": "기간 만료"}.get(st, st)
        if new == "filled":
            ctx.store.update_fill(o["id"], fq or o["qty"], ap)
        ctx.store.update_order(o["id"], new, note)
        ctx.store.log("info" if new == "filled" else "warn", "sync", f"{o['symbol']} {o['side']} {note}")
        if new == "filled" and o["side"] == "buy":
            _plan_after_fill(ctx, o, ap)
        if new == "filled" and o.get("strategy") == "ai-exit":
            ctx.store.close_plan(ctx.broker.name, o["market"], o["symbol"], "stop" if "손절" in (o.get("message") or "") else "take")
        out.append({"symbol": o["symbol"], "status": new, "note": note})
    return out


def check_loss(ctx: Context, market: Market) -> bool:
    """장중에도 하루 손실 한도를 본다. 넘으면 킬 스위치를 켠다 (신규 매수 중단, 매도·손절은 계속)."""
    if ctx.kill:
        return True
    lim = RiskLimits.from_config(ctx.cfg, market)
    today = trade_date(market)
    start = ctx.store.day_start_equity(ctx.broker.name, market.value, today)
    if not start:
        return False
    acct = ctx.broker.account(market)
    universe = set(ctx.cfg["markets"][market.value]["universe"])
    managed = acct.cash + sum(p.value for s, p in acct.positions.items() if s in universe)
    if loss_breached(start, managed, lim):
        ctx.store.set("kill_switch", "1")
        ctx.store.set("kill_since", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        ctx.store.set("kill_reason", f"{market.value} 하루 손실 {managed / start - 1:.1%} 로 한도 초과")
        ctx.store.log("error", f"risk.{market.value}", f"장중 손실 {managed / start - 1:.2%} 로 하루 한도 초과. 킬 스위치를 켰습니다")
        return True
    return False


def snapshot_equity(ctx: Context, market: Market) -> bool:
    """장 마감 뒤 하루 한 번 자산을 기록한다 (자산 추이·누적 수익률·하루 손실 기준)."""
    today = trade_date(market)
    if ctx.store.has_equity(ctx.broker.name, market.value, today):
        return False
    a = ctx.broker.account(market)
    ctx.store.add_equity(today, ctx.broker.name, market.value, a.equity, a.cash)
    return True


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
        from .principles import HOLDING
        days = pl.get("horizon") or HOLDING.get(ctx.cfg["ai"].get("holding", "auto"), {}).get("days")
        from . import guard as _guard
        from .scheduler import is_open as _reg_open
        if why in ("stop", "take") and _guard.active(ctx, market, pl["symbol"]) and (market == Market.US or _reg_open(market)):
            continue  # 토스 OCO 가 같은 가격에서 판다 (두 번 팔지 않게)
        if not why and days:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(pl["created_at"])).days
            if age >= days * 7 / 5:  # 거래일 -> 달력일 대략
                why = "time"
        if not why:
            continue
        pending = [o for o in store.rows("select * from orders where account=? and symbol=? and strategy='ai-exit' "
                                         "and status='submitted' order by id desc", (ctx.broker.name, pl["symbol"]))]
        retry = 0
        if pending:
            last = pending[0]
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(last["ts"])).total_seconds()
            if age < 180 or ctx.broker.name != "toss":
                continue  # 아직 기다리는 중
            try:
                ctx.broker.client.cancel(last["broker_order_id"])
                store.update_order(last["id"], "rejected", "3분 미체결로 취소 후 재주문")
            except Exception as e:
                store.log("error", "exit", f"{pl['symbol']} 미체결 손절 주문 취소 실패: {e}")
                continue
            retry = len(pending)
        # 국내는 시장가, 미국은 지정가만 되므로 현재가보다 낮게 (재주문할수록 1%씩 더 낮게)
        from .scheduler import is_open as _regular
        if market == Market.US:
            price = round(px * (0.997 - 0.01 * retry), 2)
        elif _regular(market):
            price = 0  # 국내 정규장: 시장가
        else:  # 국내 시간외(NXT): 지정가만 되므로 현재가보다 1% 낮게 호가 단위로
            price = kr_tick(px * (0.99 - 0.01 * retry))
        label = {"stop": "손절", "take": "익절", "time": "기간 만료"}[why]
        _guard.release(ctx, market, pl["symbol"])  # QTrader 가 직접 팔기 전에 토스 보호를 푼다
        req = OrderRequest(market, pl["symbol"], Side.SELL, pos.qty, price, "ai-exit",
                           f"{label}: 현재가 {px:,} / 손절가 {pl['stop']:,} / 익절가 {pl['take']:,}")
        r = ctx.broker.order(req)
        status = ("filled" if r.filled_qty else "submitted") if r.ok else "rejected"
        store.add_order(trade_date=today, account=ctx.broker.name, market=market.value, symbol=req.symbol, side="sell",
                        qty=req.qty, price=req.price or px, status=status, broker_order_id=r.broker_order_id,
                        strategy="ai-exit", message=f"{req.reason} | {r.message}")
        # 토스는 접수만 된 상태라 체결 확인(sync_orders) 때 계획을 닫는다. 그 전까지는 미체결 재주문 대상
        if r.ok and (status == "filled" or ctx.broker.name != "toss"):
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
    if req.side == Side.SELL:
        from . import guard
        guard.release(ctx, market, req.symbol)
    r = ctx.broker.order(req)
    status = ("filled" if r.filled_qty else "submitted") if r.ok else "rejected"
    store.add_order(trade_date=a["trade_date"], account=ctx.broker.name, market=market.value, symbol=req.symbol,
                    side=req.side.value, qty=req.qty, price=req.price, status=status, broker_order_id=r.broker_order_id,
                    strategy=req.strategy, message=f"승인 #{aid} | {req.reason} | {r.message}")
    store.decide_approval(aid, "approved", f"{status} {r.message}")
    if r.ok and req.strategy == "ai" and (status == "filled" or ctx.broker.name != "toss"):
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
    universe = list(mc["universe"])
    if use_ai and cfg["ai"].get("auto_pick", True):
        # AI 가 고른 오늘 후보 + 오늘 회의한 종목 + 가진 종목까지 (가진 종목은 손절·매도 판단이 필요)
        import json as _json
        picks = _json.loads(store.get(f"picks_{market.value}_{today}") or "[]")
        met = [r["symbol"] for r in store.ai_runs(200) if r["trade_date"] == today and r["market"] == market.value]
        try:
            held = list(ctx.broker.account(market).positions)
        except Exception:
            held = []
        universe = list(dict.fromkeys(universe + [p["symbol"] for p in picks] + met + held))
    p = panel(ctx.data, market, universe, cfg["history_days"], errors)
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
        from .principles import index_bars
        raw, keep, ai_notes = ai_targets(store, cfg, p, today, acct_id, market.value, warns, index_bars(ctx.data, market))
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
    usd_left, fx_short = None, []
    need_ok = use_ai and cfg["ai"].get("require_approval", False) and not dry_run
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
        if (market == Market.US and o.side == Side.BUY and ctx.broker.name == "toss" and not dry_run
                and cfg.get("fx_policy", "preferential_only") == "preferential_only"):
            if usd_left is None:
                try:
                    usd_left = float(ctx.broker.client.buying_power("USD"))
                except Exception:
                    usd_left = float("inf")
            need = o.qty * (o.price or prices.get(o.symbol, 0))
            if need > usd_left:
                fx_short.append((o.symbol, need - usd_left))
                store.log("warn", src, f"{o.symbol} 달러 부족(${need - usd_left:,.0f})으로 사지 않았습니다. 환전 우대로 환전해 주세요")
                results.append({"symbol": o.symbol, "side": "buy", "qty": o.qty, "price": o.price, "status": "skipped_fx",
                                "message": "달러 부족 · 환전 필요"})
                continue
            usd_left -= need
        if dry_run:
            status, res_msg, oid = "dry_run", "모의 실행(주문 안 함)", ""
        else:
            if o.side == Side.SELL:  # 토스 서버 손절·익절이 걸려 있으면 먼저 푼다 (두 번 팔리지 않게)
                from . import guard
                guard.release(ctx, market, o.symbol)
            r = ctx.broker.order(o)
            status = ("filled" if r.filled_qty else "submitted") if r.ok else "rejected"
            res_msg, oid = r.message, r.broker_order_id
        store.add_order(trade_date=today, account=acct_id, market=market.value, symbol=o.symbol,
                        side=o.side.value, qty=o.qty, price=o.price, status=status, broker_order_id=oid,
                        strategy=o.strategy, message=f"{o.reason} | {res_msg}")
        if use_ai and (status == "filled" or (status == "submitted" and ctx.broker.name != "toss")):
            remember_plan(store, acct_id, market.value, o, today)
        store.log("info" if status != "rejected" else "error", src,
                  f"{status} {o.side.value} {o.symbol} x{o.qty} @ {o.price:,.2f} {res_msg}")
        results.append({"symbol": o.symbol, "side": o.side.value, "qty": o.qty, "price": o.price,
                        "status": status, "message": res_msg})

    if fx_short:
        from .extras import notify
        notify(store, cfg, f"💱 달러가 약 ${sum(x[1] for x in fx_short):,.0f} 모자라 {len(fx_short)}종목({', '.join(x[0] for x in fx_short)})을 사지 않았어요. "
                           "토스 앱에서 환전 우대를 받아 달러로 바꿔 두면 다음 실행 때 삽니다. 비싼 자동 환전은 하지 않아요.", "fx")
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
