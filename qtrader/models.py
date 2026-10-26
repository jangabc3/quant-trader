from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Market(str, Enum):
    KR = "KR"
    US = "US"


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


CURRENCY = {Market.KR: "KRW", Market.US: "USD"}


@dataclass
class Position:
    market: Market
    symbol: str
    qty: int
    avg_price: float
    last_price: float = 0.0

    @property
    def value(self) -> float:
        return self.qty * (self.last_price or self.avg_price)


@dataclass
class Account:
    market: Market
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)

    @property
    def equity(self) -> float:
        return self.cash + sum(p.value for p in self.positions.values())


@dataclass
class OrderRequest:
    market: Market
    symbol: str
    side: Side
    qty: int
    price: float  # 0 이면 시장가 (국내만)
    strategy: str = ""
    reason: str = ""

    @property
    def notional(self) -> float:
        return self.qty * self.price


@dataclass
class OrderResult:
    ok: bool
    broker_order_id: str = ""
    message: str = ""
    filled_qty: int = 0
    filled_price: float = 0.0
