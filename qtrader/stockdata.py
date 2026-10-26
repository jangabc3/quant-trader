"""종목 화면용 데이터: 검색, 시세 요약, 호가, 체결, 여러 주기 캔들. 토스증권 키가 없으면 체험용 합성 데이터.

토스증권 캔들은 1분봉(1m)과 일봉(1d)만 준다. 5·15·60분봉은 1분봉을, 주봉·월봉은 일봉을 묶어서 만든다.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .data import synthetic
from .market import DEMO_KR, DEMO_US
from .toss import Toss, num, toss_symbol

INTERVALS = {  # 화면 주기 -> (원천, 묶을 단위, 기본 봉 수)
    "1m": ("1m", None, 200), "5m": ("1m", "5min", 120), "15m": ("1m", "15min", 100), "60m": ("1m", "60min", 60),
    "1d": ("1d", None, 250), "1w": ("1d", "W-FRI", 156), "1mo": ("1d", "MS", 120),
}
INDEX_SYMBOLS = {"KOSPI", "KOSDAQ"}
MARKETS = ["KOSPI", "KOSDAQ", "NASDAQ", "NYSE", "AMEX"]

_lock = threading.Lock()
_cache: dict[str, tuple[float, object]] = {}


def cached(key, ttl, fn):
    with _lock:
        hit = _cache.get(key)
        if hit and time.monotonic() - hit[0] < ttl:
            return hit[1]
    v = fn()
    with _lock:
        _cache[key] = (time.monotonic(), v)
    return v


# 종목 검색
def master(t: Toss | None, home: Path) -> list[dict]:
    if t is None:
        return [{"symbol": s, "name": n, "market": "KOSPI"} for s, n, _ in DEMO_KR] + \
               [{"symbol": s, "name": n, "market": "NASDAQ"} for s, n, _ in DEMO_US]

    def build():
        f = Path(home) / "stock_master.json"
        if f.exists() and time.time() - f.stat().st_mtime < 86400:
            return json.loads(f.read_text(encoding="utf-8"))
        rows = []
        for m in MARKETS:
            try:
                for x in t.stocks_all(m):
                    if x.get("securityType") in ("STOCK", "ETF", "FOREIGN_STOCK", "DEPOSITARY_RECEIPT", "REIT", "FOREIGN_ETF"):
                        rows.append({"symbol": x["symbol"], "name": x.get("name") or x["symbol"], "market": m,
                                     "type": x.get("securityType")})
            except Exception:
                continue
        if rows:
            f.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        return rows

    return cached("master", 3600, build)


def search(rows: list[dict], q: str, limit: int = 20) -> list[dict]:
    q = q.strip().upper()
    if not q:
        return []
    exact, starts, contains = [], [], []
    for r in rows:
        s, n = r["symbol"].upper(), r["name"].upper()
        if s == q or n == q:
            exact.append(r)
        elif s.startswith(q) or n.startswith(q):
            starts.append(r)
        elif q in n or q in s:
            contains.append(r)
    out = exact + sorted(starts, key=lambda r: len(r["name"])) + sorted(contains, key=lambda r: len(r["name"]))
    return out[:limit]


# 캔들
def _frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    out = df.resample(rule, label="left", closed="left").agg(agg).dropna(subset=["close"])
    return out


def _demo_candles(symbol: str, src: str, n: int) -> pd.DataFrame:
    base = 72000.0 if symbol[:1].isdigit() else 180.0
    if symbol in INDEX_SYMBOLS:
        base = 3200.0 if symbol == "KOSPI" else 880.0
    if src == "1d":
        return synthetic(symbol, days=n, start_price=base, seed=abs(hash(symbol)) % 1000)
    rng = np.random.default_rng(abs(hash(symbol + "m")) % 2**32)
    end = pd.Timestamp.now().floor("min")
    idx = pd.date_range(end=end, periods=n, freq="min")
    close = base * np.exp(np.cumsum(rng.normal(0, 0.0012, n)))
    op = np.r_[close[0], close[:-1]]
    hi = np.maximum(op, close) * (1 + np.abs(rng.normal(0, 0.0006, n)))
    lo = np.minimum(op, close) * (1 - np.abs(rng.normal(0, 0.0006, n)))
    return pd.DataFrame({"open": op, "high": hi, "low": lo, "close": close,
                         "volume": rng.integers(100, 5000, n).astype(float)}, index=idx)


def candles(t: Toss | None, symbol: str, interval: str = "1d", count: int | None = None) -> dict:
    if interval not in INTERVALS:
        raise ValueError(f"주기는 {', '.join(INTERVALS)} 중 하나")
    src, rule, default_n = INTERVALS[interval]
    n = count or default_n
    factor = {"5min": 5, "15min": 15, "60min": 60, "W-FRI": 5, "MS": 21}.get(rule, 1)
    need = min(n * factor + factor, 2400 if src == "1m" else 2600)
    is_index = symbol.upper() in INDEX_SYMBOLS

    def build():
        if t is None:
            df = _demo_candles(symbol, src, need)
        else:
            df = t.candles(symbol.upper() if is_index else symbol, src, need, indicator=is_index)
        if rule:
            df = _resample(df, rule)
        df = df.tail(n)
        intraday = src == "1m"
        rows = []
        for ts, r in df.iterrows():
            tm = int(pd.Timestamp(ts).tz_localize("Asia/Seoul").timestamp()) if intraday else pd.Timestamp(ts).strftime("%Y-%m-%d")
            rows.append({"time": tm, "open": round(float(r.open), 4), "high": round(float(r.high), 4),
                         "low": round(float(r.low), 4), "close": round(float(r.close), 4), "volume": float(r.volume)})
        return {"symbol": symbol, "interval": interval, "intraday": intraday, "demo": t is None, "candles": rows}

    return cached(f"c:{symbol}:{interval}:{n}:{t is None}", 8 if src == "1m" else 60, build)


# 호가·체결
def orderbook(t: Toss | None, symbol: str) -> dict:
    if t is None:
        rng = np.random.default_rng(int(time.time() / 3))
        mid = 72000.0 if symbol[:1].isdigit() else 180.0
        tick = 100 if symbol[:1].isdigit() else 0.01
        asks = [{"price": round(mid + tick * (i + 1), 2), "volume": int(rng.integers(100, 9000))} for i in range(10)][::-1]
        bids = [{"price": round(mid - tick * i, 2), "volume": int(rng.integers(100, 9000))} for i in range(10)]
        return {"demo": True, "asks": asks, "bids": bids, "timestamp": None}
    ob = t.orderbook(symbol)
    conv = lambda xs: [{"price": num(x.get("price")), "volume": num(x.get("volume"))} for x in xs or []]
    asks = sorted(conv(ob.get("asks")), key=lambda x: -x["price"])
    bids = sorted(conv(ob.get("bids")), key=lambda x: -x["price"])
    return {"demo": False, "asks": asks, "bids": bids, "timestamp": ob.get("timestamp")}


def trades(t: Toss | None, symbol: str) -> dict:
    if t is None:
        rng = np.random.default_rng(int(time.time() / 3))
        mid = 72000.0 if symbol[:1].isdigit() else 180.0
        now = pd.Timestamp.now()
        return {"demo": True, "items": [{"price": round(mid * (1 + rng.normal(0, 0.001)), 2), "volume": int(rng.integers(1, 500)),
                                         "time": (now - pd.Timedelta(seconds=i * 7)).isoformat()} for i in range(20)]}
    return {"demo": False, "items": [{"price": num(x.get("price")), "volume": num(x.get("volume")), "time": x.get("timestamp")}
                                     for x in t.trades(symbol, 30)]}


def summary(t: Toss | None, symbol: str) -> dict:
    """종목 머리말: 이름, 현재가, 전일 대비, 52주 범위, 시가총액, 상·하한가, 유의사항."""
    sym = symbol.upper()
    info, warns, limits = {}, [], {}
    daily = candles(t, sym, "1d", 260)["candles"]
    if t is not None:
        try:
            got = t.stocks([sym])
            info = got[0] if got else {}
        except Exception:
            info = {}
        try:
            warns = t.warnings(sym)
        except Exception:
            warns = []
        try:
            limits = t.price_limits(sym)
        except Exception:
            limits = {}
        try:
            last = t.prices([sym]).get(toss_symbol(sym)) or daily[-1]["close"]
        except Exception:
            last = daily[-1]["close"]
    else:
        demo = {s: n for s, n, _ in DEMO_KR + DEMO_US}
        info = {"symbol": sym, "name": demo.get(sym, sym), "market": "KOSPI" if sym[:1].isdigit() else "NASDAQ",
                "currency": "KRW" if sym[:1].isdigit() else "USD"}
        last = daily[-1]["close"]
    prev = daily[-2]["close"] if len(daily) > 1 else last
    closes = [d["close"] for d in daily]
    shares = num(info.get("sharesOutstanding")) or None
    return {
        "symbol": sym, "name": info.get("name") or sym, "market": info.get("market"), "currency": info.get("currency")
        or ("KRW" if sym[:1].isdigit() else "USD"), "type": info.get("securityType"), "demo": t is None,
        "price": last, "prev_close": prev, "change": last - prev, "change_rate": (last / prev - 1) if prev else 0,
        "high52": max(closes) if closes else None, "low52": min(closes) if closes else None,
        "shares": shares, "market_cap": last * shares if shares else None,
        "upper_limit": num(limits.get("upperLimitPrice")) or None, "lower_limit": num(limits.get("lowerLimitPrice")) or None,
        "warnings": [w.get("warningType") for w in warns],
        "volume_today": daily[-1]["volume"] if daily else None,
    }
