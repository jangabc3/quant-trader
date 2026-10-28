"""투자 원칙 라이브러리와 매수 전 체크리스트 (뇌동매매 방지).

체크리스트는 숫자로만 판정한다. AI 회의 결과가 '매수'여도 아래 규칙을 하나라도 어기면 매수 주문을 내지 않는다.
매도(손절·익절·비중 축소)는 위험을 줄이는 쪽이라 막지 않는다.

각 규칙은 실제 퀀트 운용사와 학계에서 널리 인용되는 연구를 근거로 한다. 다만 어떤 규칙도 수익을 보장하지 않으며,
목적은 '근거 없는 충동 매매'를 걸러내고 '틀렸을 때 손실을 작게' 만드는 것이다.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import indicators as ind

# 참고 문헌: (키, 저자·연도, 제목, 출처, 핵심, QTrader 적용)
REFERENCES = [
    ("momentum", "Jegadeesh & Titman (1993)", "Returns to Buying Winners and Selling Losers", "Journal of Finance",
     "최근 3~12개월 오른 종목이 이후 3~12개월 더 오르는 경향 (모멘텀)", "12-1개월 모멘텀이 양수일 때만 매수"),
    ("tsmom", "Moskowitz, Ooi & Pedersen (2012)", "Time Series Momentum", "Journal of Financial Economics",
     "자기 과거 12개월 수익률이 양수인 자산은 계속 오르는 경향, 하락장 방어에 효과", "12개월 수익률이 음수면 신규 매수 금지"),
    ("faber", "Faber (2007)", "A Quantitative Approach to Tactical Asset Allocation", "Journal of Wealth Management",
     "장기 이동평균(10개월≈200일) 위에서만 보유하면 큰 하락을 상당 부분 피함", "종가가 200일선 아래면 신규 매수 금지"),
    ("ff", "Fama & French (1993, 2015)", "Common Risk Factors / A Five-Factor Asset Pricing Model", "JFE",
     "가치·규모·수익성·투자 성향이 수익률을 설명하는 핵심 요인", "재무 분석가가 수익성·밸류를 점검"),
    ("vme", "Asness, Moskowitz & Pedersen (2013)", "Value and Momentum Everywhere", "Journal of Finance",
     "가치와 모멘텀은 서로 반대로 움직여 함께 쓰면 위험 대비 성과가 좋아짐", "밸류 점수와 모멘텀을 함께 확인"),
    ("gp", "Novy-Marx (2013)", "The Other Side of Value: The Gross Profitability Premium", "JFE",
     "매출총이익/자산이 높은 우량 기업이 장기적으로 더 높은 수익", "수익성(영업이익률·ROE) 점검"),
    ("qmj", "Asness, Frazzini & Pedersen (2019)", "Quality Minus Junk", "Review of Accounting Studies",
     "수익성·성장·안정성이 높은 '우량주'가 '부실주'를 이김", "부채비율·이익 안정성 점검"),
    ("fscore", "Piotroski (2000)", "Value Investing: The Use of Historical Financial Statement Information", "JAR",
     "재무제표 9개 신호(F-score)로 싼 주식 중 좋은 회사를 고르면 성과 개선", "흑자·영업현금흐름·부채 감소 확인"),
    ("bab", "Frazzini & Pedersen (2014)", "Betting Against Beta", "JFE",
     "변동성·베타가 지나치게 높은 종목은 위험만큼 보상받지 못하는 경향", "연 변동성 상한 초과 종목은 매수 금지"),
    ("volman", "Moreira & Muir (2017)", "Volatility-Managed Portfolios", "Journal of Finance",
     "변동성이 클 때 비중을 줄이면 위험 대비 성과가 좋아짐", "목표 변동성으로 포지션 크기 조절"),
    ("crash", "Daniel & Moskowitz (2016)", "Momentum Crashes", "JFE",
     "모멘텀은 급반등장에서 크게 깨질 수 있다", "과열(RSI 75 이상) 추격 매수 금지"),
    ("pead", "Bernard & Thomas (1989)", "Post-Earnings-Announcement Drift", "Journal of Accounting Research",
     "실적 서프라이즈 뒤 주가가 같은 방향으로 한동안 흐르는 경향", "실적 분석가가 서프라이즈·가이던스 점검"),
    ("kelly", "Kelly (1956) · Thorp (2006)", "A New Interpretation of Information Rate / The Kelly Criterion", "Bell System Tech. J. / Handbook",
     "이길 확률과 손익비로 최적 베팅 비율을 정한다. 실전은 절반 켈리 이하", "포지션 크기 상한 = 켈리의 절반"),
    ("odean", "Barber & Odean (2000)", "Trading Is Hazardous to Your Wealth", "Journal of Finance",
     "자주 매매하는 개인 투자자일수록 수익률이 낮다 (과잉 매매)", "하루 신규 매수 종목 수 제한, 손절 후 재매수 대기"),
    ("prospect", "Kahneman & Tversky (1979)", "Prospect Theory", "Econometrica",
     "사람은 손실을 확정하기 싫어해 손절을 미룬다 (처분 효과)", "손절가를 미리 정하고 자동 실행"),
    ("hlz", "Harvey, Liu & Zhu (2016)", "...and the Cross-Section of Expected Returns", "Review of Financial Studies",
     "수많은 '발견된' 전략 대부분은 데이터 마이닝, 더 엄격한 기준 필요", "AI 판단 기록으로 실제 성과를 계속 검증"),
    ("dsr", "Bailey & López de Prado (2014)", "The Deflated Sharpe Ratio", "Journal of Portfolio Management",
     "여러 전략을 시험하면 좋은 성과가 우연히 나온다. 시도 횟수만큼 할인해야", "과거 성과를 과신하지 않음"),
    ("afml", "López de Prado (2018)", "Advances in Financial Machine Learning", "Wiley (서적)",
     "과적합 방지, 교차검증, 메타 라벨링 등 실전 퀀트 방법론", "AI 결론을 규칙으로 한 번 더 거름(메타 라벨링 개념)"),
    ("grinold", "Grinold & Kahn (1999)", "Active Portfolio Management", "McGraw-Hill (서적)",
     "성과 = 예측력(IC) × √독립적인 판단 수. 한 번의 확신보다 꾸준한 작은 우위", "분산 투자와 종목당 비중 상한"),
    ("chan", "Ernest Chan (2008, 2013)", "Quantitative Trading / Algorithmic Trading", "Wiley (서적)",
     "개인 퀀트의 실전 절차: 검증, 리스크 관리, 켈리 기반 자금 관리", "손절·포지션 크기·일일 손실 한도"),
    # 추세·시장 국면
    ("century", "Hurst, Ooi & Pedersen (2017)", "A Century of Evidence on Trend-Following Investing", "Journal of Portfolio Management",
     "1880년대부터 100년 넘게 여러 자산에서 추세 추종이 꾸준히 성과를 냄. 큰 하락장에서 특히 손실을 줄임", "200일선 추세 규칙"),
    ("states", "Cooper, Gutierrez & Hameed (2004)", "Market States and Momentum", "Journal of Finance",
     "모멘텀 수익은 시장이 오른 뒤에만 나타나고, 시장이 하락 국면이면 사라짐", "지수가 200일선 아래면 신규 매수 비중 절반 (안정형은 매수 중단)"),
    ("moments", "Barroso & Santa-Clara (2015)", "Momentum Has Its Moments", "Journal of Financial Economics",
     "변동성으로 비중을 조절한 모멘텀은 수익은 비슷하고 급락(크래시)은 크게 줄어듦", "목표 변동성으로 종목 비중 축소"),
    # 변동성
    ("ivol", "Ang, Hodrick, Xing & Zhang (2006)", "The Cross-Section of Volatility and Expected Returns", "Journal of Finance",
     "변동성이 큰 종목일수록 이후 수익률이 오히려 낮음", "연 변동성 상한"),
    ("lowvol", "Baker, Bradley & Wurgler (2011)", "Benchmarks as Limits to Arbitrage: Understanding the Low-Volatility Anomaly", "Financial Analysts Journal",
     "저변동성 주식이 장기적으로 고변동성 주식보다 위험 대비 성과가 좋음", "변동성 상한과 변동성 목표 비중"),
    # 후보 고르기 (screener)
    ("52wh", "George & Hwang (2004)", "The 52-Week High and Momentum Investing", "Journal of Finance",
     "52주 신고가에 가까운 종목이 이후에도 더 오르는 경향. 모멘텀 효과의 상당 부분을 설명", "후보 점수에 52주 고가 근접도"),
    ("volmom", "Lee & Swaminathan (2000)", "Price Momentum and Trading Volume", "Journal of Finance",
     "거래량이 함께 늘어난 모멘텀이 더 오래 이어지거나, 관심 쏠림의 신호가 됨", "후보 점수에 거래량 증가"),
    ("reversal", "Jegadeesh (1990)", "Evidence of Predictable Behavior of Security Returns", "Journal of Finance",
     "직전 1주~1개월 급등한 종목은 단기적으로 되돌림이 잦음", "최근 5일 급등은 감점 (추격 매수 방지)"),
    # 행동재무
    ("disposition", "Odean (1998)", "Are Investors Reluctant to Realize Their Losses?", "Journal of Finance",
     "개인은 이익 난 주식은 너무 빨리 팔고 손실 난 주식은 너무 오래 들고 있음 (처분 효과)", "손절가에 닿으면 사람 판단 없이 자동 매도"),
    # 검증·과최적화
    ("mp", "McLean & Pontiff (2016)", "Does Academic Research Destroy Stock Return Predictability?", "Journal of Finance",
     "논문으로 알려진 이상 현상은 발표 뒤 수익이 크게 줄어듦", "한 신호를 맹신하지 않고 여러 규칙을 모두 통과할 때만 매수"),
    ("replicate", "Hou, Xue & Zhang (2020)", "Replicating Anomalies", "Review of Financial Studies",
     "발표된 이상 현상 대부분이 소형주 영향을 걸러내면 재현되지 않음", "검증이 많이 된 모멘텀·추세·수익성·저변동성만 사용"),
    ("crisis", "Jensen, Kelly & Pedersen (2023)", "Is There a Replication Crisis in Finance?", "Journal of Finance",
     "제대로 검증하면 주요 팩터 다수는 재현되며, 비슷한 팩터를 묶어 쓰면 더 안정적", "여러 팩터를 함께 보는 체크리스트"),
    ("pbo", "Bailey, Borwein, López de Prado & Zhu (2017)", "The Probability of Backtest Overfitting", "Journal of Computational Finance",
     "여러 번 시험해 고른 전략은 과거에만 잘 맞을 확률이 높음", "백테스트 대신 가상매매·실전 기록으로 판단"),
    # AI·머신러닝
    ("ml", "Gu, Kelly & Xiu (2020)", "Empirical Asset Pricing via Machine Learning", "Review of Financial Studies",
     "머신러닝이 수익률 예측을 개선하지만 가장 중요한 신호는 모멘텀·유동성·변동성", "AI 판단도 모멘텀·변동성 규칙을 통과해야 매수"),
    ("llm", "Lopez-Lira & Tang (2023)", "Can ChatGPT Forecast Stock Price Movements?", "SSRN 작업 논문",
     "대형 언어 모델의 뉴스 해석이 단기 수익률을 일부 예측하지만, 효과는 작고 시간이 지나며 줄어듦", "뉴스 분석은 참고, 팩트체크·숫자 규칙 없이 단독 근거로 쓰지 않음"),
]
REF = {r[0]: r for r in REFERENCES}


def references_text() -> str:
    """AI 참석자에게 주는 원칙 요약 (프롬프트용)."""
    return "\n".join(f"- {a} 「{t}」: {k} → {u}" for _, a, t, _, k, u in REFERENCES)


@dataclass
class Rule:
    key: str
    name: str
    ok: bool
    value: str
    need: str
    ref: str

    @property
    def core(self) -> bool:
        return self.key not in SOFT_RULES

    def to_dict(self) -> dict:
        r = REF.get(self.ref)
        return {"key": self.key, "name": self.name, "ok": self.ok, "value": self.value, "need": self.need,
                "ref": f"{r[1]} 「{r[2]}」" if r else "", "core": self.core}


# 추가 기준: 하나쯤 못 넘어도 비중을 절반으로 줄여 살 수 있는 것 (soft_miss_allowed 개까지).
# 모두 '조금 더 확실하게' 쪽 신호라 하나만으로 손실이 커지지 않는다. 손실을 막는 핵심(손절·손익비·장기 추세·
# 변동성·투자유의·매수 한도·재매수 대기·시장 국면)은 필수로 둔다.
# 여러 신호를 점수로 보는 방식: Piotroski(2000) F-Score 처럼 '몇 개 이상 통과'로 판단한다.
SOFT_RULES = {"tsmom", "mom", "rsi", "facts"}


DEFAULT_RULES = {
    "min_confidence": 0.6,
    "min_reward_risk": 2.0,
    "max_rsi": 75.0,
    "max_vol": 0.60,  # 연 변동성 60% 초과 금지
    "target_vol": 0.25,  # 포지션 크기 산정용 목표 변동성
    "min_fact_ok": 0.6,  # 팩트체크 '확인됨' 비율
    "max_new_buys_per_day": 3,
    "cooldown_days": 5,  # 손절 후 같은 종목 재매수 대기
    "require_trend": True,
    "require_momentum": True,
    "market_filter": "half",  # 지수가 200일선 아래면: half=비중 절반, block=신규 매수 중단, off=보지 않음
    "soft_miss_allowed": 1,  # 추가 기준(SOFT_RULES)을 이만큼까지 못 넘어도 산다. 하나마다 비중을 절반으로
}
# 가상매매 연습 기준: 실제 돈이 아닐 때만, 매수·매도·손절·익절 흐름을 시험해 볼 수 있게 몇 가지를 조금 낮춘다.
# 손절가·장기 추세·변동성·투자유의·재매수 대기 같은 손실을 막는 기준과 'AI 의견이 매수'는 그대로다. 실전에는 절대 쓰지 않는다.
PRACTICE = {"min_confidence": ("min", 0.5), "min_reward_risk": ("min", 1.5), "soft_miss_allowed": ("max", 2),
            "max_new_buys_per_day": ("max", 5)}


def practice_on(cfg: dict) -> bool:
    return cfg.get("mode") == "paper" and bool((cfg.get("ai") or {}).get("practice", True))


def rules_for(cfg: dict) -> dict:
    """매수 체크리스트에 실제로 쓰는 규칙 (설정 + 가상매매면 연습 기준)."""
    r = dict(cfg["ai"].get("rules") or {})
    if practice_on(cfg):
        full = DEFAULT_RULES | r
        for k, (how, v) in PRACTICE.items():
            r[k] = min(full[k], v) if how == "min" else max(full[k], v)
    return r


# 매매 기간. 단기일수록 손절·익절을 좁히고 오래 들고 있지 않는다 (기한이 지나면 판다)
HOLDING = {
    "auto": {"name": "AI에게 맡기기 (종목마다 알맞게)", "stop": None, "take": None, "days": None, "buys": 1},
    "long": {"name": "장기 (추세 따라 몇 주~몇 달)", "stop": None, "take": None, "days": None, "buys": 0},
    "swing": {"name": "스윙 (며칠~2주)", "stop": 5.0, "take": 10.0, "days": 10, "buys": 1},
    "short": {"name": "단기 (1~3일, 짧게 자주)", "stop": 2.5, "take": 5.0, "days": 3, "buys": 2},
}


def adjust_for_holding(final: dict | None, mode: str) -> dict | None:
    """회의 결론의 손절·익절·보유 기간을 매매 기간에 맞춘다. 손익비 2 : 1 은 유지한다."""
    if mode == "auto" and final:
        # 회의가 정한 보유 기간으로 단기·스윙·장기 중 하나를 고른다 (실적 발표·이벤트면 짧게, 추세면 길게)
        d = int(final.get("horizon_days") or 20)
        mode = "short" if d <= 3 else "swing" if d <= 10 else "long"
    h = HOLDING.get(mode or "long")
    if not final or not h or h["stop"] is None:
        return final
    f = dict(final)
    sl = min(float(f.get("stop_loss_pct") or h["stop"]), h["stop"]) or h["stop"]
    tp = min(float(f.get("take_profit_pct") or h["take"]), h["take"])
    f["stop_loss_pct"], f["take_profit_pct"] = round(sl, 2), round(max(tp, sl * 2), 2)
    f["horizon_days"] = min(int(f.get("horizon_days") or h["days"]), h["days"])
    return f


# 시장 국면을 볼 대표 지수 ETF
INDEX_ETF = {"KR": "069500", "US": "SPY"}


def index_bars(data, market) -> pd.DataFrame | None:
    """시장 국면 판단용 지수 ETF 일봉. 못 받으면 None (규칙을 건너뛴다)."""
    try:
        m = getattr(market, "value", market)
        b = data.bars(market, INDEX_ETF[m], 400)
        return b if b is not None and len(b) >= 200 else None
    except Exception:
        return None


def market_signals(bars: pd.DataFrame) -> dict:
    c = bars["close"]
    last = float(c.iloc[-1])
    sma200 = float(ind.sma(c, 200).iloc[-1]) if len(c) >= 200 else float(c.mean())
    r12 = last / float(c.iloc[-252]) - 1 if len(c) > 252 else last / float(c.iloc[0]) - 1
    r12_1 = float(c.iloc[-22]) / float(c.iloc[-252]) - 1 if len(c) > 252 else float(c.iloc[-22]) / float(c.iloc[0]) - 1 if len(c) > 22 else 0.0
    vol = float(c.pct_change().tail(60).std() * np.sqrt(252))
    return {"last": last, "sma200": sma200, "ret12": r12, "mom12_1": r12_1, "vol": vol, "rsi": float(ind.rsi(c).iloc[-1])}


def checklist(final: dict | None, check: dict | None, bars: pd.DataFrame, warnings: list[dict] | None = None,
              rules: dict | None = None, new_buys_today: int = 0, recent_stop: bool = False,
              market_bars: pd.DataFrame | None = None) -> dict:
    """매수 전 체크리스트. 모두 통과해야 매수 가능."""
    R = DEFAULT_RULES | (rules or {})
    s = market_signals(bars)
    f = final or {}
    out: list[Rule] = []
    rating = f.get("rating")
    out.append(Rule("rating", "AI 최종 의견이 매수 계열", rating in ("Buy", "Overweight"), rating or "없음",
                    "Buy 또는 Overweight", "afml"))
    conf = float(f.get("confidence") or 0)
    out.append(Rule("confidence", "AI 확신도", conf >= R["min_confidence"], f"{conf:.0%}", f"{R['min_confidence']:.0%} 이상", "grinold"))
    sl, tp = float(f.get("stop_loss_pct") or 0), float(f.get("take_profit_pct") or 0)
    rr = tp / sl if sl > 0 else 0
    out.append(Rule("reward_risk", "손익비 (익절폭 ÷ 손절폭)", rr >= R["min_reward_risk"], f"{rr:.1f} : 1",
                    f"{R['min_reward_risk']:.1f} : 1 이상", "chan"))
    out.append(Rule("stop", "손절가가 정해져 있음", 0 < sl <= 20, f"-{sl:g}%", "0~20% 사이", "prospect"))
    if R["require_trend"]:
        out.append(Rule("trend", "장기 추세 (종가 > 200일선)", s["last"] > s["sma200"],
                        f"{s['last']:,.2f} / {s['sma200']:,.2f}", "200일선 위", "faber"))
    if R["require_momentum"]:
        out.append(Rule("tsmom", "12개월 수익률 양수", s["ret12"] > 0, f"{s['ret12']:+.1%}", "0% 초과", "tsmom"))
        out.append(Rule("mom", "12-1개월 모멘텀 양수", s["mom12_1"] > 0, f"{s['mom12_1']:+.1%}", "0% 초과", "momentum"))
    out.append(Rule("rsi", "과열 추격 아님 (RSI)", s["rsi"] < R["max_rsi"], f"{s['rsi']:.0f}", f"{R['max_rsi']:.0f} 미만", "crash"))
    out.append(Rule("vol", "변동성 과다 아님", s["vol"] <= R["max_vol"], f"{s['vol']:.0%}", f"연 {R['max_vol']:.0%} 이하", "bab"))
    fc = (check or {}).get("fact_checks") or []
    ok = sum(1 for x in fc if x.get("status") == "확인됨")
    wrong = sum(1 for x in fc if x.get("status") == "틀림")
    ratio = ok / len(fc) if fc else 0
    out.append(Rule("facts", "팩트체크 통과 (틀린 주장 없음)", bool(fc) and ratio >= R["min_fact_ok"] and wrong == 0,
                    f"확인 {ok}/{len(fc)}, 틀림 {wrong}", f"확인 {R['min_fact_ok']:.0%} 이상, 틀림 0", "hlz"))
    bad = [w.get("warningType") for w in (warnings or []) if not w.get("endDate")
           or str(w.get("endDate")) >= pd.Timestamp.today().strftime("%Y-%m-%d")]
    out.append(Rule("warnings", "투자 유의·과열 지정 없음", not bad, ", ".join(bad) or "없음", "없음", "odean"))
    out.append(Rule("overtrade", "오늘 신규 매수 한도", new_buys_today < R["max_new_buys_per_day"],
                    f"{new_buys_today}건", f"{R['max_new_buys_per_day']}건 미만", "odean"))
    out.append(Rule("cooldown", "최근 손절 종목 재매수 아님", not recent_stop, "최근 손절" if recent_stop else "해당 없음",
                    f"손절 후 {R['cooldown_days']}거래일 대기", "prospect"))
    regime_cut = 1.0
    mf = R.get("market_filter", "half")
    if market_bars is not None and mf != "off":
        mc = market_bars["close"]
        m_last, m_sma = float(mc.iloc[-1]), float(ind.sma(mc, 200).iloc[-1])
        down = m_last < m_sma
        out.append(Rule("regime", "시장 국면 (지수 > 200일선)", not (down and mf == "block"),
                        f"{'하락' if down else '상승'} 국면 ({m_last / m_sma - 1:+.1%})",
                        "하락 국면이면 매수 중단" if mf == "block" else "하락 국면이면 비중 절반", "states"))
        if down:
            regime_cut = 0.5
    core, soft = [r for r in out if r.core], [r for r in out if not r.core]
    missed = sum(not r.ok for r in soft)
    allowed = int(R.get("soft_miss_allowed", 1))
    passed = all(r.ok for r in core) and missed <= allowed
    # 포지션 크기: 절반 켈리와 변동성 목표 중 작은 값
    p = conf
    kelly = max(0.0, p - (1 - p) / rr) if rr > 0 else 0.0
    vol_w = min(1.0, R["target_vol"] / s["vol"]) if s["vol"] > 0 else 1.0
    soft_cut = 0.5 ** missed  # 추가 기준을 하나 못 넘을 때마다 비중 절반
    size = min(kelly / 2, vol_w) * regime_cut * soft_cut
    return {"passed": passed, "rules": [r.to_dict() for r in out], "signals": {k: round(v, 4) for k, v in s.items()},
            "core": {"ok": sum(r.ok for r in core), "total": len(core)},
            "soft": {"ok": len(soft) - missed, "total": len(soft), "missed": missed, "allowed": allowed},
            "size": {"half_kelly": round(kelly / 2, 4), "vol_target": round(vol_w, 4), "regime": regime_cut,
                     "soft": soft_cut, "weight_factor": round(size, 4)}}


def trade_plan(final: dict | None, last: float, budget: float, max_weight: float, size_factor: float,
               market: str) -> dict | None:
    """회의 결과를 '얼마에 사서, 얼마에 팔고, 몇 주'로 바꾼다. 가격이 현재가와 동떨어지면 현재가 기준으로 다시 잡는다."""
    if not final or not last:
        return None
    lo, hi = float(final.get("entry_low") or 0), float(final.get("entry_high") or 0)
    sane = lambda p: last * 0.5 < p < last * 1.5
    if not (sane(lo) and sane(hi)) or lo > hi:
        lo = hi = last
    entry = (lo + hi) / 2
    sl, tp = float(final.get("stop_loss_pct") or 0) / 100, float(final.get("take_profit_pct") or 0) / 100
    rnd = (lambda v: round(v)) if market == "KR" else (lambda v: round(v, 2))
    stop, take = rnd(entry * (1 - sl)), rnd(entry * (1 + tp))
    amount = budget * max_weight * max(0.0, min(1.0, size_factor * 4))  # 절반 켈리 0.25 를 비중 상한 100% 로 본다
    qty = int(amount // entry) if entry else 0
    return {"price": rnd(last), "entry_low": rnd(lo), "entry_high": rnd(hi), "entry": rnd(entry), "stop": stop, "take": take,
            "stop_pct": round(sl * 100, 2), "take_pct": round(tp * 100, 2), "qty": qty,
            "amount": rnd(qty * entry), "max_loss": rnd(qty * (entry - stop)), "max_gain": rnd(qty * (take - entry)),
            "horizon_days": int(final.get("horizon_days") or 20), "currency": "KRW" if market == "KR" else "USD"}
