"""AI 회의 후보 자동 발굴. 사람이 종목을 정하지 않아도 매일 회의할 종목을 고른다.

1) 후보 풀 (넓게): 보유·관심·기본 목록 + 성장·기술주 바구니 + 토스증권 거래대금·거래량 상위,
   1주·1개월·3개월 급상승, 토스 사용자 인기, 강세 섹터 5곳의 주도주
2) 숫자 규칙 거르기: 매수 체크리스트와 같은 규칙 (200일선 위, 모멘텀 양수, 과열·변동성 상한)
3) 멀티팩터 점수 (순위 평균)
   - 12-1개월 모멘텀 (Jegadeesh & Titman 1993)
   - 52주 신고가 근접도 (George & Hwang 2004, '52-Week High and Momentum Investing')
   - 저변동성 (Ang 외 2006, Baker 외 2011)
   - 거래량 증가 = 관심 유입 (Lee & Swaminathan 2000, 'Price Momentum and Trading Volume')
   - 단기 과열 벌점: 최근 5일 급등은 되돌림이 잦다 (Jegadeesh 1990 단기 반전)
4) 공시·재무 점검 (상위 일부만, 키가 있을 때)
   - DART 최근 공시: 유상증자·전환사채·감자·불성실공시·횡령 등 악재면 제외, 공급계약·자사주 등 호재는 표시
   - 재무제표: 적자이면서 매출도 줄면 감점 (Piotroski 2000 F-Score 의 핵심 몇 가지)
AI 회의는 비용이 들어서, 여기서 걸러진 종목에만 회의를 연다. 실적·어닝콜·뉴스의 깊은 분석은 회의의 분석가들이 한다.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import numpy as np

from . import principles, vault
from .models import Market

# 대형 우량주만 보지 않도록 넣어 두는 성장·기술주 바구니 (거래가 많은 종목 위주, 숫자 규칙은 똑같이 건다)
GROWTH = {
    "KR": ["196170", "042700", "247540", "028300", "277810", "141080", "007660", "214450", "012450", "064350",
           "267260", "010120", "298040", "329180", "000660", "035420"],
    "US": ["PLTR", "CRWD", "NYSE:NET", "NYSE:SNOW", "NYSE:SHOP", "MELI", "AXON", "NYSE:ANET", "ARM", "APP", "RKLB",
           "NYSE:IONQ", "HOOD", "COIN", "NYSE:UBER", "NYSE:TSM", "AVGO", "AMD", "NVDA", "NYSE:ORCL", "NYSE:NOW", "TSLA"],
}
BAD_DISCLOSURE = ("유상증자", "전환사채", "신주인수권부사채", "감자", "불성실공시", "관리종목", "상장폐지", "횡령", "배임",
                  "회생", "거래정지", "감사의견", "투자주의환기")
GOOD_DISCLOSURE = ("단일판매", "공급계약", "자기주식취득", "자기주식 취득", "무상증자", "현금ㆍ현물배당", "영업(잠정)실적", "특허")

# 후보를 어디서 데려왔는지 (화면의 '이유'에 붙는다)
ORIGIN: dict[str, str] = {}


def pool(ctx, market: Market) -> tuple[list[str], set[str]]:
    """대형 우량주만 보지 않도록 여러 곳에서 후보를 모은다. 숫자 규칙은 뒤에서 똑같이 건다."""
    mc = ctx.cfg["markets"][market.value]
    kr = market == Market.KR
    mine = lambda s: s.split(":")[-1][:1].isdigit() == kr
    held = set()
    try:
        held = set(ctx.broker.account(market).positions)
    except Exception:
        pass
    tagged: list[tuple[str, str]] = [(s, "보유") for s in sorted(held)]
    tagged += [(s, "관심") for s in ctx.cfg.get("watchlist", []) if mine(s)]
    tagged += [(s, "기본 목록") for s in mc.get("universe", [])]
    client = getattr(ctx.data, "client", None) if getattr(ctx.data, "name", "") == "toss" else None
    if client is not None:
        from . import market as market_mod
        for kind, dur, n, tag in (("gainers", "1mo", 20, "1개월 급상승"), ("gainers", "3mo", 15, "3개월 상승 (성장 추세)"),
                                  ("gainers", "1w", 15, "1주 급상승"), ("toss_amount", "1d", 15, "토스 사용자 인기"),
                                  ("amount", "1d", 20, "거래대금 상위"), ("volume", "1d", 10, "거래량 급증")):
            try:
                tagged += [(x["symbol"], tag) for x in market_mod.rankings(client, kind, market.value, dur, n)["items"]]
            except Exception:
                pass
        try:  # 이번 주 가장 강한 섹터 5곳의 주도주 (요즘 뜨는 업종)
            for sec in client.sector_rankings("TOP_GAINERS", market.value, "1w")[:5]:
                for x in client.sector_stocks(sec.get("sectorId"), market.value)[:5]:
                    tagged.append((x["symbol"], f"강세 섹터 · {sec.get('name', '')}"))
        except Exception:
            pass
    tagged += [(s, "성장·기술주") for s in GROWTH[market.value]]
    out = []
    for s, tag in tagged:
        if s not in ORIGIN or tag == "보유":
            ORIGIN[s] = tag
        out.append(s)
    return list(dict.fromkeys(out))[:120], held


def factors(bars) -> dict:
    """멀티팩터 원재료 (순위는 candidates 에서)."""
    c, v = bars["close"], bars["volume"] if "volume" in bars else None
    sig = principles.market_signals(bars)
    hi = float(c.tail(252).max())
    r5 = float(c.iloc[-1] / c.iloc[-6] - 1) if len(c) > 6 else 0.0
    vr = float(v.tail(20).mean() / max(v.tail(60).mean(), 1e-9)) if v is not None and len(v) > 60 else 1.0
    return sig | {"near_high": sig["last"] / hi if hi else 0, "r5": r5, "vol_ratio": vr}


def _rank(vals: list[float]) -> np.ndarray:
    a = np.asarray(vals, dtype=float)
    return a.argsort().argsort() / max(len(a) - 1, 1) if len(a) > 1 else np.ones(len(a))


def disclosures(symbol: str, home) -> dict:
    """DART 최근 10일 공시 제목으로 악재·호재 표시. 키가 없거나 실패하면 빈 값."""
    key = vault.get("dart_api_key")
    if not key or not symbol[:1].isdigit():
        return {}
    try:
        import requests
        from .fundamentals import dart_corp_codes
        corp = dart_corp_codes(key, home).get(symbol)
        if not corp:
            return {}
        d = requests.get("https://opendart.fss.or.kr/api/list.json", timeout=10, params={
            "crtfc_key": key, "corp_code": corp, "bgn_de": (date.today() - timedelta(days=10)).strftime("%Y%m%d"),
            "end_de": date.today().strftime("%Y%m%d"), "page_count": 30}).json()
        titles = [x.get("report_nm", "") for x in d.get("list", [])]
    except Exception:
        return {}
    bad = next((t for t in titles if any(k in t for k in BAD_DISCLOSURE)), None)
    good = next((t for t in titles if any(k in t for k in GOOD_DISCLOSURE)), None)
    return {"bad": bad, "good": good}


def quality(ctx, symbol: str) -> dict:
    """재무 간단 점검: 적자이면서 매출 감소면 감점. 국내는 DART, 미국은 SEC (연락처를 넣었을 때)."""
    from . import config as config_mod, fundamentals as fd
    try:
        if symbol[:1].isdigit():
            key = vault.get("dart_api_key")
            if not key:
                return {}
            fin = fd.dart_financials(symbol, key, config_mod.home())
        else:
            contact = ctx.cfg.get("sec_contact")
            if not contact:
                return {}
            fin = fd.sec_financials(symbol.split(":")[-1], contact)
        r = fd.ratios(fin)
    except Exception:
        return {}
    weak = (r.get("operating_margin") or 0) < 0 and (r.get("revenue_growth") or 0) < 0
    return {"weak": weak, "growth": r.get("revenue_growth"), "margin": r.get("operating_margin")}


def _basic_filter(ctx, market: Market, rows: list[dict]) -> list[dict]:
    """토스 종목 기본 정보로 거르기: 거래정지·정리매매·상장폐지 예정, 레버리지·인버스 ETF/ETN, 우선주, 상장 6개월 안 된 종목."""
    client = getattr(ctx.data, "client", None) if getattr(ctx.data, "name", "") == "toss" else None
    if client is None:
        return rows
    syms = [r["symbol"] for r in rows if not r["held"]]
    info = {}
    for i in range(0, len(syms), 200):
        try:
            for x in client.stocks(syms[i:i + 200]):
                info[x.get("symbol")] = x
        except Exception:
            return rows
    keep = []
    half_year = (date.today() - timedelta(days=182)).isoformat()
    for r in rows:
        x = info.get(r["symbol"].split(":")[-1])
        why = None
        if x and not r["held"]:
            kd = x.get("koreanMarketDetail") or {}
            lev = x.get("leverageFactor")
            if x.get("status") != "ACTIVE" or x.get("delistDate"):
                why = "상장 상태"
            elif kd.get("krxTradingSuspended") or kd.get("liquidationTrading"):
                why = "거래정지·정리매매"
            elif lev not in (None, "", "1", "1.0") and float(lev) != 1.0:
                why = f"레버리지·인버스 ({lev}배)"
            elif x.get("isCommonShare") is False:
                why = "우선주"
            elif x.get("securityType") in ("ETN", "STOCK_WARRANTS"):
                why = x.get("securityType")
            elif x.get("listDate") and x["listDate"] > half_year:
                why = "상장 6개월 안 됨"
        if why:
            ctx.store.log("info", f"picks.{market.value}", f"{r['symbol']} 후보 제외: {why}")
        else:
            keep.append(r)
    return keep


def candidates(ctx, market: Market, n: int) -> list[dict]:
    """[{symbol, score, reason, held, fails, factors}] 좋은 순. 가진 종목이 먼저."""
    from . import config as config_mod
    R = principles.DEFAULT_RULES | (ctx.cfg["ai"].get("rules") or {})
    syms, held = pool(ctx, market)
    rows = []
    for s in syms:
        try:
            bars = ctx.data.bars(market, s, 300)
            if len(bars) < 60:
                continue
            f = factors(bars)
        except Exception:
            continue
        fails = []
        if R["require_trend"] and f["last"] <= f["sma200"]:
            fails.append("200일선 아래")
        if R["require_momentum"] and (f["ret12"] <= 0 or f["mom12_1"] <= 0):
            fails.append("모멘텀 음수")
        if f["rsi"] >= R["max_rsi"]:
            fails.append(f"과열 RSI {f['rsi']:.0f}")
        if f["vol"] > R["max_vol"]:
            fails.append(f"변동성 {f['vol']:.0%}")
        if s in held or not fails:
            rows.append({"symbol": s, "held": s in held, "fails": fails, "f": f})
    rows = _basic_filter(ctx, market, rows)
    if not rows:
        return []
    # 멀티팩터 순위 평균
    mom, hi_, lv, vr, r5 = (_rank([r["f"][k] for r in rows]) for k in ("mom12_1", "near_high", "vol", "vol_ratio", "r5"))
    for i, r in enumerate(rows):
        r["score"] = float(0.35 * mom[i] + 0.25 * hi_[i] + 0.2 * (1 - lv[i]) + 0.1 * vr[i] + 0.1 * (1 - r5[i]))
    rows.sort(key=lambda x: (not x["held"], -x["score"]))
    # 위쪽 후보만 공시·재무 점검 (호출 수를 줄이려고)
    out, home = [], config_mod.home()
    for r in rows:
        if not r["held"] and sum(not x["held"] for x in out) >= n:
            break
        s, f = r["symbol"], r["f"]
        notes = []
        d = disclosures(s, home)
        if d.get("bad") and not r["held"]:
            ctx.store.log("info", f"picks.{market.value}", f"{s} 제외: 최근 공시 '{d['bad'][:30]}'")
            continue
        if d.get("good"):
            notes.append(f"공시: {d['good'][:18]}")
        q = quality(ctx, s)
        if q.get("weak") and not r["held"]:
            r["score"] -= 0.3
            notes.append("적자·매출 감소")
        elif q.get("growth") is not None:
            notes.append(f"매출 {q['growth']:+.0%}")
        reason = " · ".join([x for x in (ORIGIN.get(s, ""),) if x] + [f"모멘텀 {f['mom12_1']:+.0%}", f"52주 고가의 {f['near_high']:.0%}"] + notes)
        out.append({"symbol": s, "score": round(r["score"], 3), "reason": reason, "held": r["held"], "fails": r["fails"],
                    "factors": {"mom": f["mom12_1"], "near_high": f["near_high"], "vol": f["vol"], "vol_ratio": f["vol_ratio"]}})
    out.sort(key=lambda x: (not x["held"], -x["score"]))
    return out


def todays(ctx, market: Market, today: str, n: int) -> list[dict]:
    """하루 한 번 고르고 저장해 둔다 (화면·엔진이 같은 목록을 본다)."""
    key = f"picks_{market.value}_{today}"
    saved = ctx.store.get(key)
    if saved:
        return json.loads(saved)
    picks = candidates(ctx, market, n)
    ctx.store.set(key, json.dumps(picks, ensure_ascii=False))
    ctx.store.log("info", f"picks.{market.value}", "오늘 AI 회의 후보: " + ", ".join(p["symbol"] for p in picks) if picks
                  else "오늘은 숫자 규칙을 통과한 후보가 없어 회의를 열지 않습니다")
    return picks
