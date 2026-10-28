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
NAMES = {"KOSPI": "코스피", "KOSDAQ": "코스닥", "KR_BOND_2Y": "국고채 2년", "KR_BOND_3Y": "국고채 3년", "KR_BOND_5Y": "국고채 5년",
         "KR_BOND_10Y": "국고채 10년", "KR_BOND_20Y": "국고채 20년", "KR_BOND_30Y": "국고채 30년",
         "SPY": "S&P500 (SPY)", "QQQ": "나스닥100 (QQQ)", "DIA": "다우 (DIA)"}

_cache: dict[str, tuple[float, object]] = {}
_inflight: dict[str, threading.Event] = {}
_names: dict[str, str] = {}
_lock = threading.Lock()


def _store(key: str, fn) -> object:
    try:
        val = fn()
        with _lock:
            _cache[key] = (time.monotonic(), val)
        return val
    finally:
        with _lock:
            ev = _inflight.pop(key, None)
        if ev:
            ev.set()


def cached(key: str, ttl: float, fn, stale: float | None = None):
    """받아 둔 값을 ttl 초 동안 그대로 쓴다. 그 뒤 stale 초(기본 ttl 의 10배)까지는 받아 둔 값을 바로 돌려주고
    새 값은 뒤에서 받는다 (토스 앱처럼 화면이 기다리지 않게). 같은 값을 여러 곳에서 동시에 찾으면 한 번만 받는다."""
    stale = ttl * 10 if stale is None else stale
    with _lock:
        hit = _cache.get(key)
        age = time.monotonic() - hit[0] if hit else None
        if hit and age < ttl:
            return hit[1]
        busy = _inflight.get(key)
        if busy is None:
            _inflight[key] = threading.Event()
        if hit and age < ttl + stale:
            if busy is None:  # 뒤에서 새로 받기 (실패하면 다음에 다시)
                threading.Thread(target=lambda: _quiet(_store, key, fn), name=f"refresh-{key[:30]}", daemon=True).start()
            return hit[1]
    if busy is not None:  # 다른 요청이 받는 중: 그 결과를 기다린다
        busy.wait(30)
        with _lock:
            hit = _cache.get(key)
        if hit:
            return hit[1]
        with _lock:
            _inflight.setdefault(key, threading.Event())
    return _store(key, fn)


def _quiet(f, *a):
    try:
        f(*a)
    except Exception:
        pass


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
    if not indicator:  # 종목은 토스가 준 현재가·기준가로 (순위표·종목 화면과 같은 숫자)
        try:
            from .toss import toss_symbol
            q = t.quotes([symbol]).get(toss_symbol(symbol)) or {}
            if q.get("price"):
                last = q["price"]
                if q.get("base"):
                    prev = q["base"]
        except Exception:
            pass
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
            bonds = pool.submit(t.indicator_prices, [s for s in INDICATORS if s.startswith("KR_BOND")])  # 2·3·5·10·20·30년
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
                "us_index": us_indices(),
                "fx": {"rate": num(f.get("rate")), "change": num(f.get("basisPoint")), "dir": f.get("rateChangeType")},
                "bonds": [{"symbol": b["symbol"], "name": NAMES.get(b["symbol"], b["symbol"]),
                           "value": num(b.get("lastPrice"))} for b in (safe(bonds, []) or [])],
            }

    return cached("overview", 20, build)


def rankings(t: Toss | None, kind: str = "amount", market: str = "KR", duration: str = "realtime",
             count: int = 30, exclude_caution: bool = False) -> dict:
    if kind not in RANK_TYPES:
        raise ValueError(f"순위 종류: {', '.join(RANK_TYPES)}")
    if kind in ("gainers", "losers") and duration == "realtime":
        duration = "1d"  # 명세: 급상승/급하락은 realtime 미지원
    if t is None:
        return demo_rankings(kind, market, count)

    def build():
        r = t.rankings(RANK_TYPES[kind], market, duration, count, exclude_caution)
        items = r.get("rankings", [])
        nm = names(t, [x["symbol"] for x in items])
        return {"demo": False, "rankedAt": r.get("rankedAt"), "kind": kind, "market": market, "duration": duration,
                "items": [{"rank": x["rank"], "symbol": x["symbol"], "name": nm.get(x["symbol"], x["symbol"]),
                           "price": num(x["price"].get("lastPrice")), "change_rate": num(x["price"].get("changeRate")),
                           "volume": num(x.get("tradingVolume")), "amount": num(x.get("tradingAmount")),
                           "currency": x.get("currency")} for x in items]}

    return cached(f"rank:{kind}:{market}:{duration}:{count}:{int(exclude_caution)}", 3, build)


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

    return {"demo": True, "asof": datetime.now(KST).isoformat(timespec="seconds"), "session": "체험용 데이터", "us_index": us_indices(),
            "indices": [idx("KOSPI", 3200.0), idx("KOSDAQ", 880.0)],
            "us": [us("SPY", 640.0), us("QQQ", 560.0), us("DIA", 450.0)],
            "fx": {"rate": round(1390 + float(rng.normal(0, 4)), 1), "change": round(float(rng.normal(0, 3)), 1), "dir": "UP"},
            "bonds": [{"symbol": f"KR_BOND_{k}", "name": NAMES[f"KR_BOND_{k}"], "value": v}
                      for k, v in (("2Y", 2.55), ("3Y", 2.61), ("5Y", 2.72), ("10Y", 2.94), ("20Y", 2.98), ("30Y", 2.91))]}


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
                      "change_rate": cr, "volume": vol, "amount": vol * px * (1 if market == "KR" else 1390),  # 토스처럼 미국도 원화 환산
                      "currency": "KRW" if market == "KR" else "USD"})
    key = {"amount": "amount", "volume": "volume", "toss_amount": "amount", "toss_volume": "volume"}.get(kind)
    items.sort(key=(lambda x: -x[key]) if key else (lambda x: -x["change_rate"] if kind == "gainers" else x["change_rate"]))
    for i, x in enumerate(items):
        x["rank"] = i + 1
    return {"demo": True, "rankedAt": None, "kind": kind, "market": market, "duration": "1d", "items": items}


# 미국 실제 지수 (토스 Open API 에는 미국 지수가 없어 야후 파이낸스 공개 차트 데이터를 쓴다. 비공식이라 실패하면 ETF 로 대신)
US_INDEX = [("^GSPC", "S&P 500"), ("^IXIC", "나스닥 종합"), ("^DJI", "다우존스")]


def us_indices() -> list[dict]:
    import os
    import requests
    if os.environ.get("QTRADER_OFFLINE"):
        return []

    def one(sym, name):
        r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}", params={"range": "1d", "interval": "5m"},
                         headers={"User-Agent": "Mozilla/5.0 QTrader"}, timeout=8)
        res = r.json()["chart"]["result"][0]
        meta, q = res["meta"], res["indicators"]["quote"][0]
        last = float(meta["regularMarketPrice"])
        prev = float(meta.get("chartPreviousClose") or meta.get("previousClose") or last)
        spark = [{"time": int(t), "value": round(float(c), 2)} for t, c in zip(res.get("timestamp") or [], q.get("close") or []) if c is not None]
        highs = [h for h in (q.get("high") or []) if h is not None]
        lows = [x for x in (q.get("low") or []) if x is not None]
        return {"symbol": sym, "name": name, "price": last, "change": last - prev, "change_rate": last / prev - 1 if prev else 0,
                "prev": prev, "high": max(highs) if highs else last, "low": min(lows) if lows else last, "spark": spark, "index": True}

    def build():
        out = []
        for s, n in US_INDEX:
            try:
                out.append(one(s, n))
            except Exception:
                return []
        return out

    return cached("us_indices", 30, build)


# 지금 뜨는 산업 (토스 섹터 랭킹 + 대표 종목)
DEMO_INDUSTRY = [("s1", "소프트웨어", 0.0858, 7558e8, 115.4e12, "SK스퀘어", 54), ("s2", "음원", 0.0833, 7336e8, 116.0e12, "SK스퀘어", 7),
                 ("s3", "온라인쇼핑", 0.0717, 8434e8, 135.5e12, "SK스퀘어", 17), ("s4", "전자부품", 0.0696, 1.9e12, 117.6e12, "삼성전기", 72),
                 ("s5", "스마트폰부품", 0.0694, 1.6e12, 108.2e12, "삼성전기", 89), ("s6", "반도체부품소재", 0.0671, 2.6e12, 183.3e12, "삼성전기", 64),
                 ("s7", "알루미늄", 0.0652, 737e8, 2.6e12, "삼아알미늄", 9), ("s8", "반도체패키징", 0.0555, 1833e8, 7.9e12, "하나마이크론", 14),
                 ("s9", "반도체장비", 0.0513, 1.3e12, 87.7e12, "한미반도체", 90), ("s10", "지주사", 0.0504, 1.3e12, 308.2e12, "SK스퀘어", 110)]


def industries(t: Toss | None, market: str = "KR", kind: str = "TOP_GAINERS", duration: str = "1d") -> dict:
    if t is None:
        items = [{"rank": i + 1, "sectorId": sid, "name": n, "changeRate": r, "tradingAmount": amt, "marketCap": cap,
                  "leader": lead, "count": cnt} for i, (sid, n, r, amt, cap, lead, cnt) in enumerate(DEMO_INDUSTRY)]
        if kind == "MARKET_TRADING_AMOUNT":
            items.sort(key=lambda x: -x["tradingAmount"])
        return {"demo": True, "items": items}

    def build():
        from concurrent.futures import ThreadPoolExecutor
        rows = t.sector_rankings(kind, market, duration)[:15]

        def lead(x):
            try:
                st = t.sector_stocks(x["sectorId"], market)
                top = max(st, key=lambda s: num(s.get("tradingAmount"))) if st else None
                return (top or {}).get("name"), (top or {}).get("symbol"), len(st)
            except Exception:
                return None, None, None
        with ThreadPoolExecutor(5) as ex:
            leads = list(ex.map(lead, rows))
        return {"demo": False, "items": [{"rank": x.get("rank"), "sectorId": x.get("sectorId"), "name": x.get("name"),
                                          "changeRate": num(x.get("changeRate")), "tradingAmount": num(x.get("tradingAmount")) or None,
                                          "marketCap": num(x.get("marketCapitalization")) or None, "leader": ld[0], "leader_symbol": ld[1],
                                          "count": ld[2]} for x, ld in zip(rows, leads)]}

    return cached(f"industries:{market}:{kind}:{duration}", 300, build)


def industry_chart(t: Toss | None, sector_id: str, market: str = "KR") -> dict:
    """산업 1년 차트: 시가총액 상위 5종목을 시총 가중으로 묶은 지수 vs 코스피 (같은 날 0% 에서 시작).
    토스 API 에 섹터 지수 시세가 없어서 구성 종목으로 만든다."""
    import pandas as pd
    if t is None:
        rng = _rng("indc" + sector_id)
        days = pd.bdate_range(end=datetime.now(KST).date(), periods=250)
        a, b = np.cumprod(1 + rng.normal(0.0012, 0.02, 250)) - 1, np.cumprod(1 + rng.normal(0.0005, 0.01, 250)) - 1
        fmt = lambda arr: [{"time": d.strftime("%Y-%m-%d"), "value": round(float(v), 4)} for d, v in zip(days, arr)]
        return {"sector": fmt(a), "index": fmt(b), "basket": []}

    def build():
        st = sorted(t.sector_stocks(sector_id, market), key=lambda s: -num(s.get("marketCapitalization")))[:5]
        series, caps = [], []
        for s in st:
            try:
                c = t.candles(s["symbol"], "1d", 250)["close"]
                series.append(c / c.iloc[0])
                caps.append(num(s.get("marketCapitalization")) or 1)
            except Exception:
                continue
        if not series:
            return {"sector": [], "index": [], "basket": []}
        df = pd.concat(series, axis=1).ffill().dropna()
        w = np.array(caps[:df.shape[1]]) / sum(caps[:df.shape[1]])
        sec = (df * w).sum(axis=1) - 1
        out = {"sector": [{"time": d.strftime("%Y-%m-%d"), "value": round(float(v), 4)} for d, v in sec.items()],
               "basket": [s.get("name") for s in st], "index": []}
        if market == "KR":
            try:
                k = t.candles("KOSPI", "1d", 250, indicator=True)["close"]
                k = k[k.index >= sec.index[0]]
                out["index"] = [{"time": d.strftime("%Y-%m-%d"), "value": round(float(v / k.iloc[0] - 1), 4)} for d, v in k.items()]
            except Exception:
                pass
        return out

    return cached(f"industry_chart:{sector_id}:{market}", 1800, build)


def industry_detail(t: Toss | None, sector_id: str, market: str = "KR") -> dict:
    """기간별 등락률 (섹터 vs 코스피) + 구성 종목 상위."""
    durs = ["1d", "1w", "1mo", "3mo", "1y"]
    if t is None:
        rng = _rng("ind" + sector_id)
        return {"demo": True, "name": dict((a, b) for a, b, *_ in DEMO_INDUSTRY).get(sector_id, sector_id),
                "perf": [{"duration": d, "sector": float(rng.normal(0.02, 0.06)) * (i + 1), "index": float(rng.normal(0.01, 0.03)) * (i + 1)}
                         for i, d in enumerate(durs)],
                "stocks": [{"symbol": s, "name": n, "price": p, "tradingAmount": None} for s, n, p in DEMO_KR[:6]],
                "etfs": [{"symbol": "091160", "name": "KODEX 반도체 (예시)", "price": 52000}, {"symbol": "305720", "name": "KODEX 2차전지산업 (예시)", "price": 18000}]}

    def build():
        d = t.sector_detail(sector_id, market)
        rates = {x.get("duration"): num(x.get("changeRate")) for x in ((d.get("performance") or {}).get("changeRates") or [])}
        idx = {}
        try:  # 비교용 코스피 기간 수익률 (지수 일봉, 최신이 앞)
            r = t._call("GET", "/api/v1/market-indicators/KOSPI/candles", "MARKET_INDICATOR", {"interval": "1d", "count": 200}) or {}
            closes = [num(c.get("closePrice") or c.get("close")) for c in (r.get("candles") or [])]
            closes = [c for c in closes if c]
            for k, n in {"1d": 1, "1w": 5, "1mo": 21, "3mo": 63}.items():
                if len(closes) > n:
                    idx[k] = closes[0] / closes[n] - 1
        except Exception:
            pass
        st = sorted(t.sector_stocks(sector_id, market), key=lambda s: -num(s.get("tradingAmount")))[:8]
        px = {}
        try:
            px = t.prices([s["symbol"] for s in st])
        except Exception:
            pass
        etfs = []
        try:
            es = t.sector_etfs(sector_id, market)[:5]
            epx = t.prices([e["symbol"] for e in es]) if es else {}
            etfs = [{"symbol": e["symbol"], "name": e.get("name"), "price": epx.get(e["symbol"])} for e in es]
        except Exception:
            pass
        return {"demo": False, "name": d.get("name"), "summary": d.get("summary"), "etfs": etfs,
                "perf": [{"duration": k, "sector": rates.get(k), "index": idx.get(k)} for k in durs],
                "stocks": [{"symbol": s["symbol"], "name": s.get("name"), "price": px.get(s["symbol"]), "tradingAmount": num(s.get("tradingAmount"))}
                           for s in st]}

    return cached(f"industry:{sector_id}:{market}", 300, build)


# 외국인·기관·개인 순매수 상위 (토스는 종목별 투자자 동향만 줘서, 거래대금 상위 종목을 모아 금액으로 줄 세운다)
def investor_flows(t: Toss | None, side: str = "buy") -> dict:
    sgn = 1 if side == "buy" else -1
    pick = lambda rows: {k: sorted(rows, key=lambda r: -sgn * r[k])[:8] for k in ("foreigner", "institution", "individual")}
    if t is None:
        rng = _rng("flows")
        rows = [{"symbol": s, "name": n, "price": p, "change_rate": float(rng.normal(0.01, 0.03)),
                 "foreigner": float(rng.normal(0, 300)) * 1e8, "institution": float(rng.normal(0, 200)) * 1e8,
                 "individual": float(rng.normal(0, 250)) * 1e8} for s, n, p in DEMO_KR]
        return {"demo": True, "updated": None, **pick(rows)}

    def build():
        from concurrent.futures import ThreadPoolExecutor
        base = rankings(t, "amount", "KR", "1d", 40)["items"]

        def one(x):
            try:
                rec = (t.stock_investor_trading(x["symbol"], 1).get("records") or [None])[0]
            except Exception:
                rec = None
            if not rec:
                return None
            net = lambda k: num((rec.get(k) or {}).get("netBuyVolume")) * (x["price"] or 0)
            return {"symbol": x["symbol"], "name": x["name"], "price": x["price"], "change_rate": x["change_rate"],
                    "foreigner": net("foreigner"), "institution": net("institution"), "individual": net("individual"),
                    "updated": rec.get("updatedAt") or rec.get("date")}
        with ThreadPoolExecutor(6) as ex:
            rows = [r for r in ex.map(one, base) if r]
        return {"updated": max((r["updated"] or "" for r in rows), default=None), "rows": rows}

    data = cached("investor_flows", 600, build)
    return {"demo": False, "updated": data["updated"], **pick(data["rows"])}


def index_daily(t: Toss | None, market: str, days: int = 260) -> list[dict]:
    """지수 일봉 [{time: 'YYYY-MM-DD', close}] 오래된 순. 국내=코스피(토스), 미국=S&P 500(야후)."""
    import os
    if market == "KR":
        if t is None:
            return []
        def build_kr():
            r = t._call("GET", "/api/v1/market-indicators/KOSPI/candles", "MARKET_INDICATOR", {"interval": "1d", "count": min(days, 200)}) or {}
            out = []
            for c in r.get("candles") or []:
                v = num(c.get("closePrice") or c.get("close"))
                d = str(c.get("timestamp") or c.get("date") or "")[:10]
                if v and d:
                    out.append({"time": d, "close": v})
            return sorted(out, key=lambda x: x["time"])
        return cached("idx_daily_kr", 600, build_kr)
    if os.environ.get("QTRADER_OFFLINE"):
        return []

    def build_us():
        import requests
        r = requests.get("https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC", params={"range": "1y", "interval": "1d"},
                         headers={"User-Agent": "Mozilla/5.0 QTrader"}, timeout=8).json()["chart"]["result"][0]
        from datetime import datetime as _dt
        return [{"time": _dt.utcfromtimestamp(ts).strftime("%Y-%m-%d"), "close": float(c)}
                for ts, c in zip(r["timestamp"], r["indicators"]["quote"][0]["close"]) if c is not None]
    try:
        return cached("idx_daily_us", 600, build_us)
    except Exception:
        return []


def sector_tree(t: Toss | None) -> list[dict]:
    """토스 산업 분류 (대·중·소분류) 나무."""
    if t is None:
        flat = [{"sectorId": "s0", "name": "IT", "depth": 0, "parentSectorId": None}, {"sectorId": "s1", "name": "소프트웨어", "depth": 1, "parentSectorId": "s0"},
                {"sectorId": "s4", "name": "전자부품", "depth": 1, "parentSectorId": "s0"}, {"sectorId": "s9", "name": "반도체장비", "depth": 2, "parentSectorId": "s4"},
                {"sectorId": "x0", "name": "소비재", "depth": 0, "parentSectorId": None}, {"sectorId": "s3", "name": "온라인쇼핑", "depth": 1, "parentSectorId": "x0"}]
    else:
        flat = cached("sector_tree", 86400, t.sectors)
    by = {}
    for s in flat:
        by.setdefault(s.get("parentSectorId"), []).append(s)
    def kids(pid):
        return [{"id": s["sectorId"], "name": s["name"], "depth": s.get("depth", 0), "children": kids(s["sectorId"])} for s in by.get(pid, [])]
    return kids(None)


def lending(t: Toss | None, symbol: str, count: int = 20) -> list[dict]:
    """대차거래: 기관끼리 주식을 빌리고 갚은 흐름 (잔고가 늘면 공매도 준비가 늘었다는 신호일 수 있음)."""
    if t is None or not symbol[:1].isdigit():
        return []
    recs = t.securities_lending(symbol, count).get("records", [])
    return [{"date": r.get("date"), "exec": num(r.get("executionQuantity")), "repay": num(r.get("repaymentQuantity")),
             "balance": num(r.get("balanceQuantity")), "amount": num(r.get("balanceAmount"))} for r in recs]
