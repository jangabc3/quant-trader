"""종목 화면용 데이터: 검색, 시세 요약, 호가, 체결, 여러 주기 캔들. 토스증권 키가 없으면 체험용 합성 데이터.

토스증권 캔들은 1분봉(1m)과 일봉(1d)만 준다. 5·15·60분봉은 1분봉을, 주봉·월봉은 일봉을 묶어서 만든다.
"""
from __future__ import annotations

import json
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


def cached(key, ttl, fn):
    """market.cached 와 같은 규칙 (받아 둔 값을 바로 보여 주고 뒤에서 새로 받기). 짧은 차트는 오래 묵히지 않는다."""
    from .market import cached as _cached
    return _cached("sd:" + key, ttl, fn, stale=ttl * 3)


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
                                     "type": x.get("securityType"),
                                     "en": x.get("englishName") or x.get("nameEn") or x.get("engName") or ""})
            except Exception:
                continue
        if rows:
            f.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        return rows

    return cached("master", 3600, build)


# 종목 검색: 띄어쓰기·대소문자·기호 무시, 초성(ㅅㅎㄴㅇ), 줄임말·영어 이름, 낱말 여러 개("sk 하이")
_CHO = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_STRIP = str.maketrans("", "", " -_·.,&()/'\"")

# 사람들이 흔히 쓰는 줄임말과 미국 종목 영어 이름 -> 종목 코드
ALIASES = {
    "삼전": "005930", "삼성": "005930", "하닉": "000660", "sk하닉": "000660", "하이닉스": "000660", "엘지엔솔": "373220",
    "엘엔솔": "373220", "lg엔솔": "373220", "현차": "005380", "기아차": "000270", "네이버": "035420", "카뱅": "323410",
    "셀트": "068270", "삼바": "207940", "삼성바이오": "207940", "포스코": "005490", "엘지전자": "066570", "엘지화학": "051910",
    "한화에어로": "012450", "에코프로비엠": "247540", "두산에너빌": "034020", "카카오뱅크": "323410", "케이비": "105560",
    "apple": "AAPL", "nvidia": "NVDA", "tesla": "TSLA", "microsoft": "MSFT", "amazon": "AMZN", "google": "GOOGL",
    "alphabet": "GOOGL", "meta": "META", "facebook": "META", "netflix": "NFLX", "broadcom": "AVGO", "intel": "INTC",
    "palantir": "PLTR", "cocacola": "KO", "coke": "KO", "tsmc": "TSM", "qualcomm": "QCOM", "micron": "MU",
    "costco": "COST", "disney": "DIS", "nike": "NKE", "starbucks": "SBUX", "mcdonalds": "MCD", "jpmorgan": "JPM",
    "visa": "V", "mastercard": "MA", "oracle": "ORCL", "salesforce": "CRM", "adobe": "ADBE", "uber": "UBER",
    "coinbase": "COIN", "berkshire": "BRK.B", "walmart": "WMT", "pepsi": "PEP", "boeing": "BA", "exxon": "XOM",
    "johnson": "JNJ", "pfizer": "PFE", "eli lilly": "LLY", "lilly": "LLY", "novo": "NVO", "arm": "ARM", "snowflake": "SNOW",
    "엔비": "NVDA", "테슬": "TSLA", "마소": "MSFT", "구글": "GOOGL", "페이스북": "META", "넷플": "NFLX", "팔란": "PLTR",
    "코카콜라": "KO", "티에스엠씨": "TSM", "애플": "AAPL",
}


def norm(text: str) -> str:
    return (text or "").upper().translate(_STRIP)


def chosung(text: str) -> str:
    out = []
    for ch in text or "":
        c = ord(ch)
        out.append(_CHO[(c - 0xAC00) // 588] if 0xAC00 <= c <= 0xD7A3 else ch)
    return "".join(out)


def _is_cho(q: str) -> bool:
    return bool(q) and all(ch in _CHO for ch in q)


_index: dict = {}


def _build_index(rows: list[dict]) -> list[tuple]:
    key = (id(rows), len(rows))
    if _index.get("key") != key:
        _index.update(key=key, rows=[(r, norm(r["symbol"].split(":")[-1]), norm(r["name"]), chosung(norm(r["name"])),
                                       norm(r.get("en") or "")) for r in rows])
    return _index["rows"]


def search(rows: list[dict], q: str, limit: int = 20) -> list[dict]:
    """좋은 순: 코드 일치 > 이름·줄임말 일치 > 앞부분 일치 > 초성 > 낱말 모두 포함 > 일부 포함. 같으면 주식 > ETF, 짧은 이름."""
    raw = (q or "").strip()
    nq = norm(raw)
    if not nq:
        return []
    words = [norm(w) for w in raw.split() if norm(w)]
    alias_exact = {norm(ALIASES[k]) for k in ALIASES if norm(k) == nq}
    alias_part = {norm(ALIASES[k]) for k in ALIASES if len(nq) >= 2 and norm(k).startswith(nq)}
    cho = _is_cho(raw.replace(" ", ""))
    scored = []
    for r, sym, name, ch, en in _build_index(rows):
        if sym == nq:
            sc = 100
        elif name == nq or sym in alias_exact or (en and en == nq):
            sc = 95
        elif sym in alias_part:
            sc = 90
        elif name.startswith(nq) or (en and en.startswith(nq)):
            sc = 80
        elif sym.startswith(nq):
            sc = 75
        elif cho and ch.startswith(nq):
            sc = 70
        elif len(words) > 1 and all(w in name or w in sym or w in en for w in words):
            sc = 60
        elif nq in name or (en and nq in en):
            sc = 50
        elif cho and nq in ch:
            sc = 45
        elif nq in sym and len(nq) >= 2:
            sc = 40
        else:
            continue
        kind = r.get("type") or ""
        scored.append((-sc, kind not in ("STOCK", "FOREIGN_STOCK", ""), len(r["name"]), r["name"], r))
    scored.sort(key=lambda x: x[:4])
    return [x[4] for x in scored[:limit]]


def resolve(rows: list[dict], text: str) -> str | None:
    """이름·줄임말로 쓴 종목을 코드로 (예: 'SK하이닉' -> '000660'). 코드 모양이면 그대로."""
    t = (text or "").strip()
    if not t:
        return None
    up = t.upper()
    if up.isdigit() and len(up) == 6 or (":" in up) or (up.replace(".", "").isalpha() and up.isascii() and len(up) <= 6
                                                           and any(norm(r["symbol"].split(":")[-1]) == norm(up) for r in rows)):
        return up
    hit = search(rows, t, 1)
    return hit[0]["symbol"] if hit else None


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
            # 한국 시각을 그대로 차트 시각으로 쓴다 (차트는 UTC 로 그리므로 KST 벽시계 값을 UTC 처럼 넘김)
            tm = int(pd.Timestamp(ts).timestamp()) if intraday else pd.Timestamp(ts).strftime("%Y-%m-%d")
            rows.append({"time": tm, "open": round(float(r.open), 4), "high": round(float(r.high), 4),
                         "low": round(float(r.low), 4), "close": round(float(r.close), 4), "volume": float(r.volume)})
        return {"symbol": symbol, "interval": interval, "intraday": intraday, "demo": t is None, "candles": rows}

    return cached(f"c:{symbol}:{interval}:{n}:{t is None}", 8 if src == "1m" else 60, build)


# 호가·체결
def orderbook(t: Toss | None, symbol: str) -> dict:
    if t is None:
        rng = np.random.default_rng(int(time.time() / 3))
        mid = candles(None, symbol, "1d")["candles"][-1]["close"]
        tick = (100 if mid >= 50000 else 10) if symbol[:1].isdigit() else 0.01
        mid = round(mid / tick) * tick
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
        mid = candles(None, symbol, "1d")["candles"][-1]["close"]
        now = pd.Timestamp.now()
        return {"demo": True, "items": [{"price": round(mid * (1 + rng.normal(0, 0.001)), 2), "volume": int(rng.integers(1, 500)),
                                         "time": (now - pd.Timedelta(seconds=i * 7)).isoformat()} for i in range(20)]}
    return {"demo": False, "items": [{"price": num(x.get("price")), "volume": num(x.get("volume")), "time": x.get("timestamp")}
                                     for x in t.trades(symbol, 30)]}


def limit_base(limits: dict, last: float, rate: float | None) -> float | None:
    """상·하한가 가운데 = 오늘 기준가 (국내 ±30%). 토스 등락률과 맞을 때만 쓴다 (호가 단위로 깎여 한 칸 안쪽 오차)."""
    up, lo = num(limits.get("upperLimitPrice")), num(limits.get("lowerLimitPrice"))
    if not (up and lo and last and rate is not None):
        return None
    mid = (up + lo) / 2
    return mid if abs(last / mid - 1 - rate) < 0.003 else None


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
        base, rate = None, None
        try:
            q = t.quotes([sym]).get(toss_symbol(sym)) or {}
            last, base, rate = q.get("price") or daily[-1]["close"], q.get("base"), q.get("change_rate")
        except Exception:
            last = daily[-1]["close"]
        base = limit_base(limits, last, rate) or base
    else:
        base, rate = None, None
        demo = {s: n for s, n, _ in DEMO_KR + DEMO_US}
        info = {"symbol": sym, "name": demo.get(sym, sym), "market": "KOSPI" if sym[:1].isdigit() else "NASDAQ",
                "currency": "KRW" if sym[:1].isdigit() else "USD"}
        last = daily[-1]["close"]
    # 기준가(전일 정규장 종가)는 토스 값을 쓴다. 일봉 두 개로 계산하면 시간외가 섞인 날 순위표와 등락률이 어긋난다
    prev = base if t is not None and base else (daily[-2]["close"] if len(daily) > 1 else last)
    closes = [d["close"] for d in daily]
    shares = num(info.get("sharesOutstanding")) or None
    return {
        "symbol": sym, "name": info.get("name") or sym, "market": info.get("market"), "currency": info.get("currency")
        or ("KRW" if sym[:1].isdigit() else "USD"), "type": info.get("securityType"), "demo": t is None,
        "price": last, "prev_close": prev, "change": last - prev,
        "change_rate": rate if rate is not None else ((last / prev - 1) if prev else 0),
        "high52": max(closes) if closes else None, "low52": min(closes) if closes else None,
        "shares": shares, "market_cap": last * shares if shares else None,
        "upper_limit": num(limits.get("upperLimitPrice")) or None, "lower_limit": num(limits.get("lowerLimitPrice")) or None,
        "warnings": [w.get("warningType") for w in warns],
        "volume_today": daily[-1]["volume"] if daily else None,
    }
