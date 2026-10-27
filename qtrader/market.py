"""시장 한눈에 보기: 지수, 투자자 수급, 환율·금리, 실시간 순위. 토스증권 시세로 만든다.

토스증권 키가 없으면 화면 구성을 볼 수 있게 '체험용' 합성 데이터를 돌려주고 demo=True 로 표시한다.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import numpy as np

from .models import Market
from .toss import INDICATORS, KST, Toss, num

RANK_TYPES = {
    "amount": "MARKET_TRADING_AMOUNT", "volume": "MARKET_TRADING_VOLUME",
    "gainers": "TOP_GAINERS", "losers": "TOP_LOSERS",
    "toss_amount": "TOSS_SECURITIES_TRADING_AMOUNT", "toss_volume": "TOSS_SECURITIES_TRADING_VOLUME",
}
US_PROXIES = ["SPY", "QQQ", "DIA"]
NAMES = {"KOSPI": "코스피", "KOSDAQ": "코스닥", "KR_BOND_3Y": "국고채 3년", "KR_BOND_10Y": "국고채 10년",
         "SPY": "S&P500 (SPY)", "QQQ": "나스닥100 (QQQ)", "DIA": "다우 (DIA)"}

_cache: dict[str, tuple[float, object]] = {}
_names: dict[str, str] = {}
_lock = threading.Lock()


def cached(key: str, ttl: float, fn):
    with _lock:
        hit = _cache.get(key)
        if hit and time.monotonic() - hit[0] < ttl:
            return hit[1]
    val = fn()
    with _lock:
        _cache[key] = (time.monotonic(), val)
    return val


def names(t: Toss, symbols: list[str]) -> dict[str, str]:
    missing = [s for s in symbols if s not in _names]
    if missing:
        try:
            for x in t.stocks(missing):
                _names[x["symbol"]] = x.get("name") or x["symbol"]
        except Exception:
            pass
    return {s: _names.get(s, s) for s in symbols}


def _change(t: Toss, symbol: str, indicator: bool) -> dict:
    df = t.candles(symbol, "1d", 2, indicator=indicator)
    last, prev = float(df["close"].iloc[-1]), float(df["close"].iloc[-2]) if len(df) > 1 else float(df["close"].iloc[-1])
    row = df.iloc[-1]
    return {"symbol": symbol, "name": NAMES.get(symbol, symbol), "price": last,
            "change": last - prev, "change_rate": (last / prev - 1) if prev else 0.0,
            "prev": prev, "high": float(row.get("high", last)), "low": float(row.get("low", last)),
            "volume": float(row.get("volume", 0) or 0)}


def _intraday(t: Toss, symbol: str) -> list[dict]:
    try:
        df = t.candles(symbol, "1m", 200, indicator=True)
    except Exception:
        return []
    today = df[df.index.date == df.index[-1].date()]
    return [{"time": int(ts.timestamp()), "value": round(float(v), 2)} for ts, v in today["close"].items()]


def _flows(t: Toss, symbol: str) -> dict:
    recs = t.market_investor_trading(symbol, 1).get("records", [])
    if not recs:
        return {}
    r = recs[0]
    net = lambda k: (num((r.get(k) or {}).get("buyAmount")) - num((r.get(k) or {}).get("sellAmount"))) / 1e8
    return {"date": r.get("date"), "individual": round(net("individual")), "foreigner": round(net("foreigner")),
            "institution": round(net("institution")), "unit": "억원"}


def session_label(t: Toss) -> str:
    try:
        cal = t.calendar(Market.KR)
        reg = ((cal.get("today") or {}).get("integrated") or {}).get("regularMarket")
        if not reg:
            return "휴장일"
        now = datetime.now(KST)
        s, e = datetime.fromisoformat(reg["startTime"]), datetime.fromisoformat(reg["endTime"])
        return f"정규장 {s:%H:%M}~{e:%H:%M} · " + ("장중" if s <= now < e else ("장 시작 전" if now < s else "장 마감"))
    except Exception:
        return ""


def overview(t: Toss | None) -> dict:
    if t is None:
        return demo_overview()

    def build():
        with ThreadPoolExecutor(6) as pool:
            idx = {s: pool.submit(_change, t, s, True) for s in ("KOSPI", "KOSDAQ")}
            us = {s: pool.submit(_change, t, s, False) for s in US_PROXIES}
            spark = {s: pool.submit(_intraday, t, s) for s in ("KOSPI", "KOSDAQ", *US_PROXIES)}
            flows = {s: pool.submit(_flows, t, s) for s in ("KOSPI", "KOSDAQ")}
            fx = pool.submit(t.exchange_rate)
            bonds = pool.submit(t.indicator_prices, [s for s in INDICATORS if s.startswith("KR_BOND")])
            sess = pool.submit(session_label, t)

            def safe(f, default=None):
                try:
                    return f.result()
                except Exception:
                    return default

            indices = []
            for s in ("KOSPI", "KOSDAQ"):
                d = safe(idx[s])
                if d:
                    d["spark"] = safe(spark[s], [])
                    d["flows"] = safe(flows[s], {})
                    indices.append(d)
            f = safe(fx, {}) or {}
            return {
                "demo": False, "asof": datetime.now(KST).isoformat(timespec="seconds"), "session": safe(sess, ""),
                "indices": indices, "us": [x | {"spark": safe(spark[x["symbol"]], [])} for x in (safe(us[s]) for s in US_PROXIES) if x],
                "fx": {"rate": num(f.get("rate")), "change": num(f.get("basisPoint")), "dir": f.get("rateChangeType")},
                "bonds": [{"symbol": b["symbol"], "name": NAMES.get(b["symbol"], b["symbol"]),
                           "value": num(b.get("lastPrice"))} for b in (safe(bonds, []) or [])],
            }

    return cached("overview", 20, build)


def rankings(t: Toss | None, kind: str = "amount", market: str = "KR", duration: str = "realtime",
             count: int = 30) -> dict:
    if kind not in RANK_TYPES:
        raise ValueError(f"순위 종류: {', '.join(RANK_TYPES)}")
    if kind in ("gainers", "losers") and duration == "realtime":
        duration = "1d"  # 명세: 급상승/급하락은 realtime 미지원
    if t is None:
        return demo_rankings(kind, market, count)

    def build():
        r = t.rankings(RANK_TYPES[kind], market, duration, count)
        items = r.get("rankings", [])
        nm = names(t, [x["symbol"] for x in items])
        return {"demo": False, "rankedAt": r.get("rankedAt"), "kind": kind, "market": market, "duration": duration,
                "items": [{"rank": x["rank"], "symbol": x["symbol"], "name": nm.get(x["symbol"], x["symbol"]),
                           "price": num(x["price"].get("lastPrice")), "change_rate": num(x["price"].get("changeRate")),
                           "volume": num(x.get("tradingVolume")), "amount": num(x.get("tradingAmount")),
                           "currency": x.get("currency")} for x in items]}

    return cached(f"rank:{kind}:{market}:{duration}:{count}", 15, build)


def watch_quotes(t: Toss | None, symbols: list[str]) -> list[dict]:
    if t is None or not symbols:
        return []

    def build():
        out = []
        nm = names(t, [s.split(":")[-1] for s in symbols])
        for s in symbols:
            try:
                d = _change(t, s, False)
                d["name"] = nm.get(s.split(":")[-1], s)
                out.append(d)
            except Exception:
                continue
        return out

    return cached("watch:" + ",".join(symbols), 20, build)


# 체험용 합성 데이터 (실제 시세 아님)
def _rng(key: str):
    return np.random.default_rng(abs(hash((key, datetime.now().strftime("%Y%m%d%H%M")))) % 2**32)


def demo_overview() -> dict:
    rng = _rng("ov")

    def idx(sym, base):
        steps = rng.normal(0, base * 0.0008, 200).cumsum()
        start = int(datetime.now(KST).replace(hour=9, minute=0, second=0, microsecond=0).timestamp())
        spark = [{"time": start + i * 60, "value": round(base + float(v), 2)} for i, v in enumerate(steps)]
        last = spark[-1]["value"]
        return {"symbol": sym, "name": NAMES[sym], "price": last, "change": last - base, "change_rate": last / base - 1,
                "spark": spark, "flows": {"individual": int(rng.integers(-15000, 15000)),
                                          "foreigner": int(rng.integers(-12000, 12000)),
                                          "institution": int(rng.integers(-5000, 5000)), "unit": "억원"}}

    def us(sym, base):
        steps = rng.normal(0, base * 0.0004, 390).cumsum()
        start = int(datetime.now(KST).replace(hour=22, minute=30, second=0, microsecond=0).timestamp()) - 86400
        spark = [{"time": start + i * 60, "value": round(base + float(v), 2)} for i, v in enumerate(steps)]
        last = spark[-1]["value"]
        vals = [p["value"] for p in spark]
        return {"symbol": sym, "name": NAMES[sym], "price": last, "change": last - base, "change_rate": last / base - 1,
                "prev": base, "high": max(vals), "low": min(vals), "volume": float(rng.integers(20, 90)) * 1e6, "spark": spark}

    return {"demo": True, "asof": datetime.now(KST).isoformat(timespec="seconds"), "session": "체험용 데이터",
            "indices": [idx("KOSPI", 3200.0), idx("KOSDAQ", 880.0)],
            "us": [us("SPY", 640.0), us("QQQ", 560.0), us("DIA", 450.0)],
            "fx": {"rate": round(1390 + float(rng.normal(0, 4)), 1), "change": round(float(rng.normal(0, 3)), 1), "dir": "UP"},
            "bonds": [{"symbol": "KR_BOND_3Y", "name": NAMES["KR_BOND_3Y"], "value": 2.61},
                      {"symbol": "KR_BOND_10Y", "name": NAMES["KR_BOND_10Y"], "value": 2.94}]}


DEMO_KR = [("005930", "삼성전자", 72000), ("000660", "SK하이닉스", 210000), ("373220", "LG에너지솔루션", 380000),
           ("005380", "현대차", 245000), ("035420", "NAVER", 198000), ("035720", "카카오", 42000),
           ("068270", "셀트리온", 185000), ("105560", "KB금융", 98000), ("012450", "한화에어로스페이스", 820000),
           ("042660", "한화오션", 98000)]
DEMO_US = [("NVDA", "엔비디아", 182.5), ("TSLA", "테슬라", 410.0), ("AAPL", "애플", 248.0), ("MSFT", "마이크로소프트", 520.0),
           ("AMZN", "아마존", 228.0), ("META", "메타", 760.0), ("GOOGL", "알파벳 A", 245.0), ("AMD", "AMD", 172.0),
           ("PLTR", "팔란티어", 180.0), ("AVGO", "브로드컴", 340.0)]


def demo_rankings(kind: str, market: str, count: int) -> dict:
    rng = _rng(kind + market)
    base = DEMO_KR if market == "KR" else DEMO_US
    items = []
    for sym, nm, px in base[:count]:
        cr = float(rng.normal(0, 0.04))
        vol = float(rng.integers(200_000, 30_000_000))
        items.append({"symbol": sym, "name": nm, "price": round(px * (1 + cr), 0 if market == "KR" else 2),
                      "change_rate": cr, "volume": vol, "amount": vol * px,
                      "currency": "KRW" if market == "KR" else "USD"})
    key = {"amount": "amount", "volume": "volume", "toss_amount": "amount", "toss_volume": "volume"}.get(kind)
    items.sort(key=(lambda x: -x[key]) if key else (lambda x: -x["change_rate"] if kind == "gainers" else x["change_rate"]))
    for i, x in enumerate(items):
        x["rank"] = i + 1
    return {"demo": True, "rankedAt": None, "kind": kind, "market": market, "duration": "1d", "items": items}
