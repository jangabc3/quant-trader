"""일봉 데이터 공급원: KIS 시세, CSV 파일, 체험용 합성 데이터."""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

import numpy as np
import pandas as pd

from .kis import KIS
from .models import Market
from .storage import Store

COLS = ["open", "high", "low", "close", "volume"]


class DataSource(Protocol):
    name: str

    def bars(self, market: Market, symbol: str, days: int) -> pd.DataFrame: ...

    def price(self, market: Market, symbol: str) -> float: ...


def file_name(symbol: str) -> str:
    return symbol.replace(":", "_") + ".csv"


class CSVData:
    """<폴더>/KR/005930.csv, <폴더>/US/NYSE_KO.csv. 열: date,open,high,low,close,volume"""

    name = "csv"

    def __init__(self, root: Path):
        self.root = Path(root)

    def bars(self, market, symbol, days):
        p = self.root / market.value / file_name(symbol)
        if not p.exists():
            raise FileNotFoundError(f"{p} 가 없습니다. CSV 를 넣거나 KIS 키를 등록하세요")
        df = pd.read_csv(p)
        df.columns = [c.strip().lower() for c in df.columns]
        df.index = pd.to_datetime(df.pop("date"))
        return df[COLS].astype(float).sort_index().tail(days)

    def price(self, market, symbol):
        return float(self.bars(market, symbol, 1)["close"].iloc[-1])


class KISData:
    name = "kis"

    def __init__(self, client: KIS, store: Store):
        self.client, self.store = client, store

    def bars(self, market, symbol, days):
        df = self.client.daily_bars(market, symbol, days)
        self.store.save_bars(market.value, symbol, df)
        return df

    def price(self, market, symbol):
        return self.client.price(market, symbol)


def synthetic(symbol: str, days: int = 750, start_price: float = 100.0, seed: int | None = None,
              end: str | pd.Timestamp | None = None) -> pd.DataFrame:
    """국면이 바뀌는 랜덤워크. 화면·전략을 키 없이 체험하는 용도이고 실제 시세가 아니다."""
    rng = np.random.default_rng(seed if seed is not None else abs(hash(symbol)) % 2**32)
    idx = pd.bdate_range(end=pd.Timestamp(end) if end else pd.Timestamp.today().normalize(), periods=days)
    drift = np.repeat(rng.normal(0.0004, 0.0015, days // 60 + 1), 60)[:days]
    rets = drift + rng.normal(0, 0.017, days)
    close = start_price * np.exp(np.cumsum(rets))
    open_ = close * np.exp(rng.normal(0, 0.004, days))
    high = np.maximum(open_, close) * np.exp(np.abs(rng.normal(0, 0.006, days)))
    low = np.minimum(open_, close) * np.exp(-np.abs(rng.normal(0, 0.006, days)))
    vol = rng.integers(100_000, 2_000_000, days).astype(float)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": vol}, index=idx)


def write_demo_csv(root: Path, cfg: dict) -> list[Path]:
    out = []
    for m, mc in cfg["markets"].items():
        base = 50_000.0 if m == "KR" else 150.0
        d = Path(root) / m
        d.mkdir(parents=True, exist_ok=True)
        for i, s in enumerate(mc["universe"]):
            df = synthetic(s, start_price=base * (1 + 0.3 * i), seed=1000 + i + (0 if m == "KR" else 500))
            if m == "KR":
                df[["open", "high", "low", "close"]] = df[["open", "high", "low", "close"]].round(0)
            p = d / file_name(s)
            df.rename_axis("date").to_csv(p, float_format="%.4f")
            out.append(p)
    return out


def panel(source: DataSource, market: Market, universe: list[str], days: int,
          errors: list[str] | None = None) -> dict[str, pd.DataFrame]:
    out = {}
    for s in universe:
        try:
            df = source.bars(market, s, days)
            if len(df):
                out[s] = df
        except Exception as e:  # 한 종목 실패가 전체를 멈추지 않게
            if errors is not None:
                errors.append(f"{s}: {e}")
    return out
