"""투자 성향 검사. 증권사 적합성 진단처럼 질문에 답하면 5단계 유형(안정형~공격투자형)을 정하고,
그 유형에 맞춰 자동매매 기준(종목당 비중, 하루 손실 한도, 매수 체크리스트)을 바꾼다.

유형이 공격적이어도 손절·킬 스위치·체크리스트 같은 안전장치는 없어지지 않는다. 기준의 엄격함만 달라진다.
"""
from __future__ import annotations

import copy
from datetime import datetime

QUESTIONS = [
    {"id": "age", "q": "나이가 어떻게 되세요?", "a": [("20대 이하", 4), ("30대", 4), ("40대", 3), ("50대", 2), ("60대 이상", 1)]},
    {"id": "exp", "q": "주식 투자 경험은요?", "a": [("거의 없어요", 1), ("1년 미만", 2), ("1~3년", 3), ("3년 이상", 4), ("5년 이상 직접 매매", 5)]},
    {"id": "goal", "q": "투자로 가장 원하는 것은?", "a": [("원금은 꼭 지키고 싶어요", 1), ("예금보다 조금만 더", 2),
                                                 ("시장 평균 정도", 3), ("시장보다 높은 수익", 4), ("위험을 감수하고 큰 수익", 5)]},
    {"id": "horizon", "q": "이 돈을 얼마나 오래 투자할 수 있나요?", "a": [("6개월 미만", 1), ("6개월~1년", 2), ("1~3년", 3), ("3~5년", 4), ("5년 이상", 5)]},
    {"id": "loss", "q": "투자금이 얼마까지 줄어도 버틸 수 있나요?", "a": [("-5%도 힘들어요", 1), ("-10%까지", 2), ("-20%까지", 3),
                                                        ("-30%까지", 4), ("-40% 이상도 기다릴 수 있어요", 5)]},
    {"id": "share", "q": "이 투자금은 전체 자산 중 어느 정도인가요?", "a": [("50% 이상", 1), ("30~50%", 2), ("10~30%", 3), ("10% 미만", 4)]},
    {"id": "drop", "q": "산 주식이 한 달 만에 20% 떨어지면?", "a": [("바로 다 판다", 1), ("일부 판다", 2), ("그대로 기다린다", 3), ("더 산다", 4)]},
    {"id": "style", "q": "어떤 방식이 더 편하세요?", "a": [("여러 종목에 나눠서 (분산형)", 0), ("확신 있는 몇 종목에 집중 (집중형)", 0)]},
]

TYPES = [  # (이름, 점수 하한, 설명)
    ("안정형", 0, "원금 손실을 최소화하는 것이 가장 중요합니다. 아주 확실할 때만 조금씩 삽니다."),
    ("안정추구형", 13, "손실은 작게, 예금보다 조금 높은 수익을 원합니다. 기준을 엄격하게 둡니다."),
    ("위험중립형", 18, "수익과 위험의 균형을 원합니다. 근거가 충분하면 적당한 비중으로 삽니다."),
    ("적극투자형", 23, "시장보다 높은 수익을 위해 어느 정도 손실을 감수합니다."),
    ("공격투자형", 28, "큰 수익을 위해 큰 변동도 견딥니다. 안전장치는 그대로, 기준만 덜 엄격합니다."),
]

# 유형별 자동매매 기준
PRESETS = {
    "안정형": {"max_weight": 0.10, "daily_loss_limit": 0.015, "rules": {"min_confidence": 0.75, "min_reward_risk": 2.5,
             "max_vol": 0.30, "max_new_buys_per_day": 1, "max_rsi": 70, "require_trend": True, "require_momentum": True, "market_filter": "block"}},
    "안정추구형": {"max_weight": 0.12, "daily_loss_limit": 0.02, "rules": {"min_confidence": 0.70, "min_reward_risk": 2.2,
               "max_vol": 0.40, "max_new_buys_per_day": 2, "max_rsi": 72, "require_trend": True, "require_momentum": True, "market_filter": "block"}},
    "위험중립형": {"max_weight": 0.15, "daily_loss_limit": 0.025, "rules": {"min_confidence": 0.65, "min_reward_risk": 2.0,
               "max_vol": 0.50, "max_new_buys_per_day": 2, "max_rsi": 75, "require_trend": True, "require_momentum": True, "market_filter": "half"}},
    "적극투자형": {"max_weight": 0.20, "daily_loss_limit": 0.03, "rules": {"min_confidence": 0.60, "min_reward_risk": 1.8,
               "max_vol": 0.60, "max_new_buys_per_day": 3, "max_rsi": 78, "require_trend": True, "require_momentum": True, "market_filter": "half"}},
    "공격투자형": {"max_weight": 0.25, "daily_loss_limit": 0.04, "rules": {"min_confidence": 0.55, "min_reward_risk": 1.6,
               "max_vol": 0.75, "max_new_buys_per_day": 4, "max_rsi": 80, "require_trend": False, "require_momentum": True, "market_filter": "half"}},
}
LOSS_TEXT = {1: "-5%", 2: "-10%", 3: "-20%", 4: "-30%", 5: "-40% 이상"}


def score(answers: dict) -> tuple[int, str]:
    total = 0
    for q in QUESTIONS:
        i = answers.get(q["id"])
        if i is None or not 0 <= int(i) < len(q["a"]):
            raise ValueError(f"'{q['q']}' 에 답해 주세요")
        total += q["a"][int(i)][1]
    name = [t for t in TYPES if total >= t[1]][-1][0]
    return total, name


def apply(cfg: dict, answers: dict) -> tuple[dict, dict]:
    """답변으로 유형을 정하고 설정에 반영한 새 설정과 결과를 돌려준다."""
    total, name = score(answers)
    p = copy.deepcopy(PRESETS[name])
    spread = int(answers.get("style", 0)) == 0
    if spread:  # 분산형: 한 종목 비중을 낮추고 종목 수를 늘린다
        p["max_weight"] = min(p["max_weight"], 0.12)
        p["rules"]["max_new_buys_per_day"] += 1
    else:  # 집중형: 한 종목 비중을 조금 높인다 (상한 30%)
        p["max_weight"] = min(p["max_weight"] + 0.05, 0.30)
    new = copy.deepcopy(cfg)
    new["risk"]["max_weight"] = p["max_weight"]
    new["risk"]["daily_loss_limit"] = p["daily_loss_limit"]
    new["ai"]["rules"] = (new["ai"].get("rules") or {}) | p["rules"]
    new["ai"]["min_confidence"] = p["rules"]["min_confidence"]
    loss = LOSS_TEXT.get(QUESTIONS[4]["a"][int(answers["loss"])][1], "")
    goal = QUESTIONS[2]["a"][int(answers["goal"])][0]
    horizon = QUESTIONS[3]["a"][int(answers["horizon"])][0]
    new["ai"]["profile"] = (f"- 투자 성향: {name} ({'분산형' if spread else '집중형'})\n- 목표: {goal}\n- 투자 기간: {horizon}\n"
                            f"- 감당 가능한 손실: {loss}\n- 레버리지·공매도·옵션은 하지 않는다\n"
                            "- 어려운 용어는 쉽게 풀어 설명해 주길 원한다")
    new["investor"] = {"type": name, "score": total, "style": "분산형" if spread else "집중형",
                       "answers": answers, "decided_at": datetime.now().isoformat(timespec="minutes")}
    return new, {"type": name, "score": total, "style": new["investor"]["style"],
                 "desc": dict((t[0], t[2]) for t in TYPES)[name], "criteria": criteria(new)}


def criteria(cfg: dict) -> list[dict]:
    """지금 설정에서 자동매매가 따르는 기준을 사람이 읽는 문장으로."""
    from .principles import DEFAULT_RULES
    r = DEFAULT_RULES | (cfg["ai"].get("rules") or {})
    risk = cfg["risk"]
    out = [
        {"icon": "pie-chart", "k": "한 종목 최대 비중", "v": f"{risk['max_weight']:.0%}", "why": "한 종목이 틀려도 계좌 전체가 크게 흔들리지 않게"},
        {"icon": "shield-alert", "k": "하루 손실 한도", "v": f"-{risk['daily_loss_limit']:.1%}", "why": "넘으면 긴급 정지가 켜져 그날 새로 사지 않음 (손절·익절은 계속)"},
        {"icon": "brain", "k": "AI 확신도", "v": f"{r['min_confidence']:.0%} 이상", "why": "AI 회의의 최종 확신도가 이보다 낮으면 사지 않음"},
        {"icon": "scale", "k": "손익비", "v": f"{r['min_reward_risk']:.1f} : 1 이상", "why": "예상 이익이 예상 손실의 이 배수 이상일 때만"},
        {"icon": "trending-up", "k": "추세", "v": "200일선 위" if r["require_trend"] else "제한 없음", "why": "장기 하락 추세에서는 사지 않음 (Faber 2007)"},
        {"icon": "rocket", "k": "모멘텀", "v": "12개월 수익률 양수" if r["require_momentum"] else "제한 없음", "why": "최근 1년 오른 종목만 (Jegadeesh & Titman 1993)"},
        {"icon": "cloud-sun", "k": "시장 국면", "v": {"block": "하락장엔 매수 중단", "half": "하락장엔 비중 절반", "off": "보지 않음"}[r.get("market_filter", "half")],
         "why": "지수가 200일선 아래면 모멘텀이 잘 안 통함 (Cooper 외 2004)"},
        {"icon": "thermometer", "k": "과열", "v": f"RSI {r['max_rsi']:.0f} 미만", "why": "너무 급하게 오른 종목 추격 금지"},
        {"icon": "activity", "k": "변동성", "v": f"연 {r['max_vol']:.0%} 이하", "why": "지나치게 출렁이는 종목 제외"},
        {"icon": "badge-check", "k": "팩트체크", "v": f"확인 {r['min_fact_ok']:.0%} 이상, 틀린 주장 0", "why": "AI 근거가 사실인지 원자료로 확인"},
        {"icon": "repeat", "k": "하루 신규 매수", "v": f"{r['max_new_buys_per_day']}종목까지", "why": "과잉 매매 방지 (Barber & Odean 2000)"},
        {"icon": "timer", "k": "손절 후 재매수", "v": f"{r['cooldown_days']}거래일 대기", "why": "손실 직후 감정적인 복수 매매 방지"},
        {"icon": "scissors", "k": "손절·익절", "v": "자동" if cfg["ai"].get("auto_exit", True) else "수동", "why": "회의가 정한 가격에 닿으면 자동으로 팔기"},
        {"icon": "hand", "k": "주문 승인", "v": "내가 승인" if cfg["ai"].get("require_approval", True) else "자동", "why": "AI 주문을 근거 확인 뒤 실행"},
    ]
    group = {"한 종목 최대 비중": "money", "하루 손실 한도": "money", "하루 신규 매수": "money", "주문 승인": "money",
             "손절·익절": "sell", "손절 후 재매수": "sell"}
    for c in out:
        c["g"] = group.get(c["k"], "buy")
    return out
