"""주문 직전 리스크 검사. 전략이 무엇을 원하든 여기를 통과한 주문만 나간다."""
from __future__ import annotations

from dataclasses import dataclass, field

from .models import Market, OrderRequest, Side


@dataclass
class RiskLimits:
    max_weight: float = 0.20
    max_exposure: float = 0.95
    max_order_value: float = float("inf")
    daily_loss_limit: float = 0.03

    @staticmethod
    def from_config(cfg: dict, market: Market) -> "RiskLimits":
        r = cfg["risk"]
        return RiskLimits(r["max_weight"], r["max_exposure"],
                          float(r.get("max_order_value", {}).get(market.value, float("inf"))),
                          r["daily_loss_limit"])


def cap_weights(weights: dict[str, float], lim: RiskLimits) -> dict[str, float]:
    w = {s: min(max(v, 0.0), lim.max_weight) for s, v in weights.items()}
    total = sum(w.values())
    if total > lim.max_exposure:
        w = {s: v * lim.max_exposure / total for s, v in w.items()}
    return w


def loss_breached(start_equity: float | None, equity: float, lim: RiskLimits) -> bool:
    if not start_equity or start_equity <= 0:
        return False
    return equity / start_equity - 1 <= -lim.daily_loss_limit


@dataclass
class Check:
    allowed: list[OrderRequest] = field(default_factory=list)
    blocked: list[tuple[OrderRequest, str]] = field(default_factory=list)


def check_orders(orders: list[OrderRequest], lim: RiskLimits, universe: set[str], kill: bool,
                 already_today: set[str], cash: float) -> Check:
    out = Check()
    for o in orders:
        if o.symbol not in universe and o.side == Side.BUY:
            out.blocked.append((o, "허용 종목 목록에 없음"))
        elif kill and o.side == Side.BUY:
            out.blocked.append((o, "킬 스위치 켜짐: 신규 매수 중단"))
        elif o.symbol in already_today:
            out.blocked.append((o, "오늘 이미 주문한 종목"))
        elif o.side == Side.BUY and o.notional > cash:
            out.blocked.append((o, f"현금 부족 ({o.notional:,.0f} > {cash:,.0f})"))
        else:
            if o.side == Side.BUY:
                cash -= o.notional
            out.allowed.append(o)
    return out


def clamp_qty(qty: int, price: float, lim: RiskLimits) -> int:
    if price <= 0:
        return 0
    return max(0, min(qty, int(lim.max_order_value // price)))
