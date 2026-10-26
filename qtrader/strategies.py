"""전략.

모든 전략은 `weights(panel)` 하나만 구현한다. panel 은 {종목: OHLCV DataFrame}.
돌려주는 값은 (날짜 x 종목) 목표 비중 DataFrame 이다. t일 행은 t일 종가까지의
정보만 써서 만든 비중이고, 실행은 다음 거래일에 한다. 백테스트와 실매매가
같은 함수를 쓰므로 둘의 결과가 어긋나지 않는다.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from . import indicators as ind


def close_frame(panel: dict[str, pd.DataFrame], col: str = "close") -> pd.DataFrame:
    return pd.DataFrame({s: df[col] for s, df in panel.items()}).sort_index()


class Strategy:
    name = "base"

    def weights(self, panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
        raise NotImplementedError

    def params(self) -> dict:
        return asdict(self) if hasattr(self, "__dataclass_fields__") else {}

    def latest(self, panel: dict[str, pd.DataFrame]) -> dict[str, float]:
        w = self.weights(panel)
        if w.empty:
            return {}
        row = w.iloc[-1].fillna(0.0)
        return {s: float(v) for s, v in row.items()}


@dataclass
class TrendFollowing(Strategy):
    """단기 이평이 장기 이평 위면 보유. 변동성 목표로 비중을 줄인다."""

    fast: int = 20
    slow: int = 60
    vol_lookback: int = 20
    target_vol: float = 0.25  # 연율. 0 이면 변동성 조절 끔
    name = "trend"

    def weights(self, panel):
        close = close_frame(panel)
        n = max(len(close.columns), 1)
        on = (close.apply(ind.sma, n=self.fast) > close.apply(ind.sma, n=self.slow)).astype(float)
        w = on / n
        if self.target_vol > 0:
            vol = close.pct_change().rolling(self.vol_lookback).std() * np.sqrt(252)
            scale = (self.target_vol / vol).clip(upper=1.0).fillna(0.0)
            w = w * scale
        return w.fillna(0.0)


@dataclass
class MomentumRank(Strategy):
    """최근 lookback 일 수익률 상위 top_k 를 동일 비중으로. 수익률이 음수면 현금."""

    lookback: int = 120
    skip: int = 5  # 최근 며칠은 반전이 잦아 제외
    top_k: int = 5
    rebalance_days: int = 20
    name = "momentum"

    def weights(self, panel):
        close = close_frame(panel)
        mom = close.shift(self.skip) / close.shift(self.lookback) - 1
        w = pd.DataFrame(np.nan, index=close.index, columns=close.columns)
        start = self.lookback
        for i in range(start, len(close), self.rebalance_days):
            row = mom.iloc[i].dropna()
            row = row[row > 0].sort_values(ascending=False).head(self.top_k)
            w.iloc[i] = 0.0
            if len(row):
                w.loc[close.index[i], row.index] = 1.0 / self.top_k
        return w.ffill().fillna(0.0)


@dataclass
class MeanReversion(Strategy):
    """RSI 과매도 또는 볼린저 하단 이탈에 진입, RSI 회복 또는 중심선 복귀에 청산."""

    rsi_n: int = 14
    entry_rsi: float = 30.0
    exit_rsi: float = 55.0
    bb_n: int = 20
    bb_k: float = 2.0
    max_hold: int = 15  # 이 거래일이 지나면 손절/익절 상관없이 청산
    name = "meanrev"

    def weights(self, panel):
        close = close_frame(panel)
        n = max(len(close.columns), 1)
        out = pd.DataFrame(0.0, index=close.index, columns=close.columns)
        for sym in close.columns:
            c = close[sym]
            r = ind.rsi(c, self.rsi_n).to_numpy()
            lo, mid, _ = ind.bollinger(c, self.bb_n, self.bb_k)
            lo, mid, cv = lo.to_numpy(), mid.to_numpy(), c.to_numpy()
            held, days = False, 0
            col = np.zeros(len(c))
            for i in range(len(c)):
                if np.isnan(r[i]) or np.isnan(mid[i]):
                    continue
                if not held and (r[i] < self.entry_rsi or cv[i] < lo[i]):
                    held, days = True, 0
                elif held:
                    days += 1
                    if r[i] > self.exit_rsi or cv[i] >= mid[i] or days >= self.max_hold:
                        held = False
                col[i] = 1.0 / n if held else 0.0
            out[sym] = col
        return out


REGISTRY: dict[str, type[Strategy]] = {
    "trend": TrendFollowing,
    "momentum": MomentumRank,
    "meanrev": MeanReversion,
}


def build(name: str, params: dict | None = None) -> Strategy:
    if name not in REGISTRY:
        raise ValueError(f"알 수 없는 전략: {name} (가능: {', '.join(REGISTRY)})")
    cls = REGISTRY[name]
    allowed = set(cls.__dataclass_fields__)
    return cls(**{k: v for k, v in (params or {}).items() if k in allowed})
