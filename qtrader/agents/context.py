"""회의 자료: 한 종목의 가격·지표·수급을 숫자로 정리한다. LLM 은 이 숫자를 근거로 말한다."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .. import indicators as ind
from ..models import Market


def market_of(symbol: str) -> Market:
    s = symbol.split(":")[-1]
    return Market.KR if s[:1].isdigit() else Market.US


def pct(a: float, b: float) -> float | None:
    return round((a / b - 1) * 100, 2) if b else None


@dataclass
class Brief:
    symbol: str
    market: Market
    name: str
    facts: dict
    flows: list[dict]
    recent: list[dict]
    levels: dict

    def text(self) -> str:
        f = self.facts
        cur = "원" if self.market == Market.KR else "달러"
        lines = [
            f"종목: {self.name} ({self.symbol}, {'국내' if self.market == Market.KR else '미국'})",
            f"기준일: {f['date']}  종가: {f['close']:,} {cur}",
            f"수익률(%): 1일 {f['ret_1d']}, 5일 {f['ret_5d']}, 20일 {f['ret_20d']}, 60일 {f['ret_60d']}, 120일 {f['ret_120d']}",
            f"이동평균 대비(%): 20일 {f['vs_sma20']}, 60일 {f['vs_sma60']}, 120일 {f['vs_sma120']}",
            f"RSI(14): {f['rsi14']}  볼린저 위치(0=하단,1=상단): {f['bb_pos']}  ATR(14)/종가: {f['atr_pct']}%",
            f"52주 고가 대비 {f['from_high']}%, 저가 대비 +{f['from_low']}%",
            f"거래량: 최근 5일 평균이 20일 평균의 {f['vol_ratio']}배  연 변동성 {f['vol_annual']}%",
            "최근 10거래일 종가: " + ", ".join(f"{r['date'][5:]} {r['close']:,}" for r in self.recent),
        ]
        lv = self.levels
        lines.append(f"가격대: 지지 {', '.join(f'{x:,}' for x in lv['support']) or '-'} / 저항 {', '.join(f'{x:,}' for x in lv['resistance']) or '-'}"
                     f" / 20·60·120일선 {lv['sma20']:,} · {lv['sma60']:,} · {lv['sma120']:,} / 52주 고가 {lv['high52']:,} 저가 {lv['low52']:,}")
        if self.flows:
            lines.append("투자자별 순매수(주, 최신순): " + " / ".join(
                f"{r['date'][5:]} 개인 {r['individual']} 외국인 {r['foreigner']} 기관 {r['institution']}"
                for r in self.flows))
        return "\n".join(lines)


def _r(v, n=2):
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else round(float(v), n)


def build(symbol: str, bars: pd.DataFrame, name: str = "", flows_raw: dict | None = None) -> Brief:
    if len(bars) < 30:
        raise ValueError(f"{symbol}: 일봉이 {len(bars)}개뿐이라 분석할 수 없습니다 (30개 이상 필요)")
    c, h, l, v = bars["close"], bars["high"], bars["low"], bars["volume"]
    last = float(c.iloc[-1])

    def back(n):
        return float(c.iloc[-1 - n]) if len(c) > n else float(c.iloc[0])

    lo, mid, hi = ind.bollinger(c)
    band = float(hi.iloc[-1] - lo.iloc[-1]) if not np.isnan(hi.iloc[-1]) else 0
    year = c.tail(252)
    facts = {
        "date": c.index[-1].strftime("%Y-%m-%d"), "close": round(last, 2),
        "ret_1d": pct(last, back(1)), "ret_5d": pct(last, back(5)), "ret_20d": pct(last, back(20)),
        "ret_60d": pct(last, back(60)), "ret_120d": pct(last, back(120)),
        "vs_sma20": pct(last, _r(ind.sma(c, 20).iloc[-1]) or 0), "vs_sma60": pct(last, _r(ind.sma(c, 60).iloc[-1]) or 0),
        "vs_sma120": pct(last, _r(ind.sma(c, 120).iloc[-1]) or 0),
        "rsi14": _r(ind.rsi(c).iloc[-1], 1),
        "bb_pos": _r((last - lo.iloc[-1]) / band) if band else None,
        "atr_pct": _r(ind.atr(h, l, c).iloc[-1] / last * 100),
        "from_high": pct(last, float(year.max())), "from_low": round(-(pct(float(year.min()), last) or 0), 2),
        "vol_ratio": _r(v.tail(5).mean() / v.tail(20).mean()) if v.tail(20).mean() else None,
        "vol_annual": _r(c.pct_change().tail(60).std() * np.sqrt(252) * 100, 1),
    }
    levels = key_levels(bars)
    recent = [{"date": d.strftime("%Y-%m-%d"), "close": round(float(x), 2)} for d, x in c.tail(10).items()]
    flows = []
    for r in (flows_raw or {}).get("records", [])[:5]:
        g = lambda k: (r.get(k) or {}).get("netBuyVolume", "-")
        flows.append({"date": r.get("date", ""), "individual": g("individual"), "foreigner": g("foreigner"),
                      "institution": g("institution")})
    return Brief(symbol, market_of(symbol), name or symbol, facts, flows, recent, levels)


def key_levels(bars: pd.DataFrame, window: int = 5, n: int = 3) -> dict:
    """최근 120거래일의 스윙 고점·저점으로 저항·지지 가격대를 잡는다 (현재가 위 저항, 아래 지지)."""
    tail = bars.tail(120)
    h, l, c = tail["high"], tail["low"], bars["close"]
    last = float(c.iloc[-1])
    highs = [float(h.iloc[i]) for i in range(window, len(h) - window)
             if h.iloc[i] == h.iloc[i - window:i + window + 1].max()]
    lows = [float(l.iloc[i]) for i in range(window, len(l) - window)
            if l.iloc[i] == l.iloc[i - window:i + window + 1].min()]

    def merge(xs):  # 1.5% 안쪽으로 붙은 가격대는 하나로
        out: list[float] = []
        for x in sorted(xs):
            if out and abs(x / out[-1] - 1) < 0.015:
                out[-1] = (out[-1] + x) / 2
            else:
                out.append(x)
        return out

    rnd = (lambda v: round(v)) if last >= 1000 else (lambda v: round(v, 2))
    res = [rnd(x) for x in merge(highs) if x > last][:n]
    sup = [rnd(x) for x in sorted(merge(lows), reverse=True) if x < last][:n]
    year = c.tail(252)
    sma = lambda k: rnd(float(ind.sma(c, k).iloc[-1])) if len(c) >= k else rnd(float(c.mean()))
    return {"support": sup, "resistance": res, "sma20": sma(20), "sma60": sma(60), "sma120": sma(120),
            "high52": rnd(float(year.max())), "low52": rnd(float(year.min()))}
