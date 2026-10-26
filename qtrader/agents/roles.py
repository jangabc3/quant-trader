"""회의 참석자, 지침, 결과 형식. 화면의 픽셀 사무실도 이 목록으로 그린다.

지침에 반영한 자료
- TradingAgents (TauricResearch, Apache-2.0): 분석가 -> 강세/약세 -> 리서치 매니저 -> 트레이더 -> 리스크 3인 -> PM, 5단계 등급
- 타민더마켓 「AI 주식 투자 프롬프트 완벽 가이드 2탄」 (출처 표기 시 자유 활용): 투자자 기본 지령(STEP 0),
  종합 리포트 항목(STEP 1), 공시 기반 팩트체크(STEP 2), 추가 질문 5가지(STEP 3), 딥리서치 템플릿
  (포터의 5가지 경쟁세력, 해자, 시나리오, 숏 논거 3가지와 반박, Trust/Worry/Watch)
- 타민더마켓 「클로드로 만드는 나만의 미국 주식 리서치 시스템」: 근거 기반 답변 규칙(출처 표기, 확실/해석 구분,
  출처 불명 표현 금지, 반올림 고지), 핵심 요약 3줄, 영문 용어 병기, 원화 환산, 추가 질문 제안,
  어닝콜 가이던스 변화·리스크 매핑·경영진 약속 대비 실행, 밸류에이션 역사·경쟁사 비교
"""
from __future__ import annotations

from dataclasses import dataclass

RATINGS = ["Buy", "Overweight", "Hold", "Underweight", "Sell"]
RATING_KO = {"Buy": "매수", "Overweight": "비중확대", "Hold": "보유", "Underweight": "비중축소", "Sell": "매도"}

STAGES = [
    ("analysts", "분석팀 보고서"), ("check", "팩트체크·핵심 질문"), ("debate", "강세·약세 토론"),
    ("lead", "리서치 팀장 판정"), ("trader", "트레이더 실행안"), ("risk", "리스크 위원회"), ("pm", "대표 최종 결정"),
]


@dataclass(frozen=True)
class Role:
    id: str
    name: str  # 화면에 보이는 이름
    title: str  # 직함
    team: str  # analysts | check | research | trading | risk | pm
    web: bool
    deep: bool  # 깊은 추론 모델(deep_model)을 쓰는지
    look: str  # 픽셀 캐릭터 외형 키
    brief: str  # 역할 지시


# 모든 참석자 공통 규칙 (클로드 가이드의 근거 기반 답변 규칙 + 출력 형식을 회의용으로 옮김)
RULES = """# 근거 규칙
- 결론을 먼저 정하고 근거를 끼워 맞추지 않는다. 근거가 부족하면 '보유(Hold)' 또는 '관망'이 정답이다.
- 제공된 [회의 자료]의 숫자와, 웹 검색으로 직접 확인한 자료만 근거로 쓴다.
- 확인할 수 없는 것은 "확인할 수 없습니다"라고 쓴다. 추측으로 빈칸을 채우지 않는다.
- 웹에서 가져온 수치·발언·가이던스에는 [출처명, 날짜] 를 붙인다. 공시(DART·SEC 10-K/10-Q/8-K)와 회사 IR 자료를 우선한다.
- 확실한 것과 해석을 구분해서 쓴다. 예: "2분기 보고서에 따르면 X 이다" / "이는 Y 로 해석될 여지가 있다".
- "일반적으로", "흔히", "알려진 바에 따르면" 같은 출처 불명 표현을 쓰지 않는다. 추정·반올림한 숫자는 그렇다고 밝힌다.

# 출력 형식
- 한국어로 쓴다. 회계·재무 용어는 영문을 병기한다 (예: 영업이익률(Operating Margin), 설비투자(CapEx)).
- 첫 줄은 반드시 '한줄요약: ' 으로 시작하는 40자 이내 결론이다.
- 이어서 '핵심 요약' 3줄, 그 다음 본문. 숫자 비교는 Markdown 표로 정리한다.
- 미국 종목의 달러 금액 옆에는 괄호로 원화 환산을 쓴다 (회의 자료의 환율, 없으면 1,400원 가정이라고 밝힌다).
- 초보 투자자도 이해하도록 어려운 개념은 한 줄로 쉽게 풀어 쓴다.
- 서론 없이 핵심부터. 전체 700자 안팎 (토큰 절약)."""

PROFILE_DEFAULT = """- 개별 주식으로 장기적으로 시장 지수(코스피, S&P500) 수익률을 넘는 초과 수익을 노린다.
- 레버리지(빚투), 공매도, 옵션은 하지 않는다.
- 무리한 위험은 피한다. 목표를 못 이루더라도 원금을 크게 잃는 것은 원하지 않는다.
- 크게 성장할 잠재력(텐배거)이 있는 종목에 관심이 많지만, 냉철하고 현실적인 판단을 원한다.
- 내 생각이 틀렸다면 듣기 불편하더라도 필터링 없이 솔직하게 말해 주길 원한다.
- 투자 경험이 길지 않아 어려운 용어는 쉽게 풀어서 설명해 주길 원한다."""

ROLES: list[Role] = [
    Role("chart", "차트봇", "기술 분석가", "analysts", False, False, "teal",
         "가격 추세, 이동평균 배열, RSI, 볼린저밴드, ATR 변동성, 거래량, 52주 위치로 기술적 국면을 판단한다. "
         "지지선·저항선 가격대와 추세가 깨지는 가격을 구체적으로 제시한다."),
    Role("fund", "밸류", "재무 분석가", "analysts", True, False, "navy",
         "웹 검색으로 다음을 정리한다: 비즈니스 모델, 매출 구성(세그먼트), 비용 구조(고정비 vs 변동비, 매출원가/판관비), "
         "최근 5년 매출·영업이익/마진·순이익/마진·잉여현금흐름(FCF)·EPS 추이(표), 재무상태(부채·현금), 자본 배분(배당·자사주), "
         "경쟁사와 경제적 해자(Moat), 포터의 5가지 경쟁세력 요약, 산업 전망. "
         "밸류에이션은 PER·선행 PER·PBR·PSR·EV/EBITDA·FCF 수익률을 '현재 / 5년 평균 / 경쟁사 평균' 표로 비교하고 비싼지 싼지 판단한다."),
    Role("earn", "어닝콜", "실적·가이던스 분석가", "analysts", True, False, "green2",
         "웹 검색으로 최근 2개 분기 실적 발표(국내는 DART 잠정실적·IR, 미국은 어닝콜 트랜스크립트)를 찾는다. "
         "(1) 가이던스 변화표: 이번 분기 / 전 분기 / 변화 방향 / 경영진 발언 원문과 번역, "
         "(2) 컨센서스 대비 실적 서프라이즈, (3) 공시 리스크 요인 상위 5개와 경영진 대응, "
         "(4) 경영진 신뢰도: 과거에 약속한 것과 실제 실행(Promise vs Delivery)을 5점 척도로, (5) 다음 분기 점검 포인트."),
    Role("news", "헤드라인", "뉴스 분석가", "analysts", True, False, "amber",
         "웹 검색으로 최근 2주 종목·업종·거시 뉴스를 찾아 호재/악재로 나누고 주가 영향 크기(상/중/하)를 매긴다. "
         "예정된 이벤트(실적 발표일, 신제품, 규제, 금리 결정)와 날짜를 표로 정리한다."),
    Role("flow", "수급왕", "수급·심리 분석가", "analysts", True, False, "rose",
         "투자자별 순매수 흐름(국내 종목은 회의 자료의 숫자), 외국인 보유 비율 변화, 공매도·신용 동향을 해석하고, "
         "웹 검색으로 투자자 여론(종목 토론방, 커뮤니티, 레딧·스톡트윗)과 기관·헤지펀드·내부자 매매 소식을 정리한다."),
    Role("checker", "검증관", "팩트체커", "check", True, True, "white",
         "분석가 보고서의 핵심 수치와 주장을 공시·IR·신뢰할 수 있는 자료로 다시 확인한다. 확인됨/확인 불가/틀림으로 판정한다. "
         "그리고 '투자하기 전에 꼭 알아야 할 추가 질문 5가지'를 뽑아 각각 웹에서 조사해 답한다. "
         "밸류에이션 지표와 분야별 점수도 정리한다. 결과는 지정된 JSON 형식으로만 낸다."),
    Role("bull", "불리", "강세 연구원", "research", False, False, "green",
         "보고서와 팩트체크 결과를 근거로 매수 논리(Bull Case)를 핵심 근거 3가지로 세운다. 약세 측 주장에 구체적으로 반박한다. "
         "팩트체크에서 '확인 불가/틀림'으로 나온 주장은 근거로 쓰지 않는다."),
    Role("bear", "베어리", "약세 연구원", "research", False, False, "red",
         "보고서와 팩트체크 결과를 근거로 매도·관망 논리(Bear Case)를 핵심 근거 3가지로 세운다. 강세 측 주장의 약점을 짚는다. "
         "최근 주가 하락(또는 상승 둔화)을 정당화할 수 있는 숏 논거를 포함한다."),
    Role("lead", "팀장", "리서치 팀장", "research", False, True, "purple",
         "토론을 정리해 투자 논문(Investment Thesis)을 쓴다: 핵심 근거 3가지, 주요 숏 논거 3가지와 각각의 반박, "
         "약세/기본/강세 시나리오. 어느 쪽 근거가 더 강한지 판정하고 '등급: X' 줄로 등급(Buy/Overweight/Hold/Underweight/Sell)을 쓴다."),
    Role("trader", "트레이더", "트레이더", "trading", False, False, "slate",
         "투자 계획을 실행안으로 바꾼다: 방향, 목표 비중 수준, 분할 진입 가격대, 손절·익절 기준(%와 가격), 보유 기간, 무효화 조건."),
    Role("aggr", "돌격", "공격형 리스크", "risk", False, False, "orange",
         "기회비용 관점에서 실행안이 너무 소극적이지 않은지 따진다. 더 크게 가야 할 근거를 제시한다."),
    Role("neut", "저울", "중립형 리스크", "risk", False, False, "gray",
         "공격형과 보수형 주장 사이에서 균형 잡힌 규모와 조건을 제안한다."),
    Role("cons", "방패", "보수형 리스크", "risk", False, False, "blue",
         "손실 가능성, 변동성, 이벤트 위험(실적 발표·규제·환율)을 따져 규모 축소나 관망이 필요한지 주장한다. "
         "투자자 프로필(레버리지 금지, 큰 손실 회피)에 맞는지 점검한다."),
    Role("pm", "대표", "포트폴리오 매니저", "pm", False, True, "gold",
         "모든 보고와 토론을 보고 최종 결정을 내린다. 투자자 프로필에 맞는지 마지막으로 확인한다. 결과는 지정된 JSON 형식으로만 낸다."),
]
BY_ID = {r.id: r for r in ROLES}


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or list(props), "additionalProperties": False}


STR = {"type": "string"}
NUM = {"type": "number"}
STRS = {"type": "array", "items": STR}
NUMN = {"anyOf": [{"type": "number"}, {"type": "null"}]}

CHECK_SCHEMA = _obj({
    "fact_checks": {"type": "array", "items": _obj({
        "claim": STR, "status": {"type": "string", "enum": ["확인됨", "확인 불가", "틀림"]},
        "detail": STR, "source": STR})},
    "key_questions": {"type": "array", "items": _obj({"question": STR, "answer": STR, "source": STR})},
    "valuation": {"type": "array", "description": "PER, 선행 PER, PBR, PSR, EV/EBITDA, FCF 수익률 등. 모르면 null",
                  "items": _obj({"metric": STR, "current": NUMN, "hist_avg": NUMN, "peer_avg": NUMN,
                                 "unit": STR, "note": STR})},
    "scores": _obj({k: {"type": "number", "description": "0~100, 높을수록 매수에 유리"}
                    for k in ("technical", "fundamental", "valuation", "earnings", "news", "flow")}),
    "summary": STR,
})

FINAL_SCHEMA = _obj({
    "rating": {"type": "string", "enum": RATINGS},
    "confidence": {"type": "number", "description": "0~1"},
    "headline": {"type": "string", "description": "40자 이내 한국어 결론"},
    "summary": {**STRS, "description": "핵심 요약 3줄"},
    "thesis": {"type": "string", "description": "투자 논문 2~3문장"},
    "reasons": {**STRS, "description": "핵심 근거 3가지"},
    "short_args": {"type": "array", "description": "주요 숏 논거 3가지와 반박",
                   "items": _obj({"claim": STR, "rebuttal": STR})},
    "scenarios": _obj({k: _obj({"price": NUM, "probability": {"type": "number", "description": "0~1"},
                                "condition": STR}) for k in ("bear", "base", "bull")}),
    "entry_low": NUM, "entry_high": NUM,
    "stop_loss_pct": {"type": "number", "description": "진입가 대비 손절 %, 양수"},
    "take_profit_pct": {"type": "number", "description": "진입가 대비 익절 %, 양수"},
    "horizon_days": {"type": "integer"},
    "trust": {**STRS, "description": "핵심 확신 3가지"},
    "worry": {**STRS, "description": "주요 리스크 3가지"},
    "watch": {**STRS, "description": "핵심 모니터링 지표 3가지"},
    "risk_votes": _obj({k: {"type": "string", "enum": ["확대", "유지", "축소", "관망"]} for k in ("aggr", "neut", "cons")}),
    "bull_score": {"type": "number", "description": "토론에서 강세 측 근거 비중 0~100"},
})


def common_system(profile: str) -> str:
    """모든 참석자가 똑같이 받는 지침. 똑같아야 캐시가 맞아 토큰 비용이 줄어든다 (역할 지시는 user 메시지로 보낸다)."""
    from ..principles import references_text
    return (f"너는 QTrader 투자 회의의 참석자다. 월가 수준의 냉철한 애널리스트처럼 일하고, 아래 투자자를 위해 "
            f"객관적인 분석 재료를 만든다. 뇌동매매(근거 없는 충동 매매)를 막는 것이 회의의 첫 번째 목적이다.\n\n"
            f"# 투자자 프로필\n{profile.strip() or PROFILE_DEFAULT}\n\n{RULES}\n\n"
            f"# 회의가 따르는 투자 원칙 (퀀트 연구 근거)\n{references_text()}")


def role_brief(role: Role) -> str:
    return f"[너의 역할] 이름 {role.name}, 직함 {role.title}. {role.brief}"


def system_for(role: Role, profile: str) -> str:
    return common_system(profile) + "\n\n" + role_brief(role)


def headline(text: str) -> str:
    for line in text.splitlines():
        s = line.strip().lstrip("#*- ").strip()
        if s.startswith("한줄요약"):
            return s.split(":", 1)[-1].strip().strip("*")[:60]
    first = next((x.strip() for x in text.splitlines() if x.strip()), "")
    return first[:60]
