"""일봉 백테스트.

체결 가정: t일 종가 기준으로 정한 비중을 t일 종가 무렵에 체결한다.
실매매 엔진도 장 마감 직전(기본 국내 15:10, 미국 15:45 ET)에 그날 가격으로 돌기 때문에
같은 가정이다. 그래서 수익은 (t일 비중) x (t+1일 수익률) 로 계산한다.
비용은 비중 변화량(회전율)에 수수료+슬리피지, 매도분에 거래세를 매긴다.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .models import Market
from .strategies import Strategy, close_frame


@dataclass
class Costs:
    fee: float  # 편도 수수료율
    slippage: float  # 편도 슬리피지
    sell_tax: float  # 매도 시 거래세

    @staticmethod
    def default(market: Market) -> "Costs":
        if market == Market.KR:
            return Costs(fee=0.00015, slippage=0.0005, sell_tax=0.0020)
        return Costs(fee=0.0025, slippage=0.0005, sell_tax=0.0)


@dataclass
class Result:
    equity: pd.Series
    benchmark: pd.Series
    weights: pd.DataFrame
    metrics: dict

    def to_dict(self) -> dict:
        def series(s: pd.Series):
            return [{"time": d.strftime("%Y-%m-%d"), "value": round(float(v), 6)} for d, v in s.items()]

        last = self.weights.iloc[-1] if len(self.weights) else pd.Series(dtype=float)
        return {
            "metrics": self.metrics,
            "equity": series(self.equity),
            "benchmark": series(self.benchmark),
            "last_weights": {k: round(float(v), 4) for k, v in last.items() if v > 0},
        }


def metrics(equity: pd.Series, turnover: pd.Series | None = None) -> dict:
    rets = equity.pct_change().dropna()
    if len(rets) == 0:
        return {}
    years = max(len(rets) / 252, 1e-9)
    total = equity.iloc[-1] / equity.iloc[0] - 1
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1
    vol = rets.std() * np.sqrt(252)
    sharpe = (rets.mean() * 252) / vol if vol > 0 else 0.0
    dd = equity / equity.cummax() - 1
    downside = rets[rets < 0].std() * np.sqrt(252)
    out = {
        "total_return": round(float(total), 4),
        "cagr": round(float(cagr), 4),
        "volatility": round(float(vol), 4),
        "sharpe": round(float(sharpe), 2),
        "sortino": round(float((rets.mean() * 252) / downside), 2) if downside > 0 else 0.0,
        "max_drawdown": round(float(dd.min()), 4),
        "win_days": round(float((rets > 0).mean()), 4),
        "days": int(len(rets)),
    }
    if turnover is not None:
        out["annual_turnover"] = round(float(turnover.sum() / years), 2)
    return out


def run(strategy: Strategy, panel: dict[str, pd.DataFrame], market: Market,
        costs: Costs | None = None, max_weight: float = 1.0, max_exposure: float = 1.0,
        initial: float = 1.0) -> Result:
    costs = costs or Costs.default(market)
    close = close_frame(panel).ffill()
    w = strategy.weights(panel).reindex(close.index).fillna(0.0).clip(upper=max_weight)
    gross = w.sum(axis=1)
    over = gross > max_exposure
    w.loc[over] = w.loc[over].mul(max_exposure / gross[over], axis=0)

    rets = close.pct_change().fillna(0.0)
    held = w.shift(1).fillna(0.0)
    port = (held * rets).sum(axis=1)

    delta = w.diff().fillna(w)
    turnover = delta.abs().sum(axis=1)
    sells = (-delta.clip(upper=0)).sum(axis=1)
    cost = turnover * (costs.fee + costs.slippage) + sells * costs.sell_tax
    equity = initial * (1 + port - cost).cumprod()

    bench = initial * (1 + rets.mean(axis=1)).cumprod()
    m = metrics(equity, turnover)
    m["benchmark_cagr"] = metrics(bench).get("cagr", 0.0)
    m["benchmark_max_drawdown"] = metrics(bench).get("max_drawdown", 0.0)
    return Result(equity=equity, benchmark=bench, weights=w, metrics=m)
