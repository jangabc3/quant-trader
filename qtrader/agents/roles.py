"""회의 참석자, 지침, 결과 형식. 화면의 픽셀 사무실도 이 목록으로 그린다.

지침에 반영한 자료
- TradingAgents (TauricResearch, Apache-2.0): 분석가 -> 강세/약세 -> 리서치 매니저 -> 트레이더 -> 리스크 3인 -> PM, 5단계 등급.
  QTrader 는 같은 자료를 여러 번 읽는 역할을 합쳐 7명으로 줄였다 (재무+실적 = 밸류, 뉴스+수급 = 헤드라인,
  팀장·트레이더·리스크 3인 = 대표). 손절·비중·하루 손실은 숫자 규칙이 정하므로 리스크 위원을 따로 두지 않는다.
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
RATING_KO = {"Buy": "매수", "Overweight": "비중확대", "Hold": "보류", "Underweight": "비중축소", "Sell": "매도"}

STAGES = [
    ("analysts", "분석팀 보고서"), ("check", "팩트체크"), ("debate", "강세·약세 토론"), ("pm", "대표 최종 결정"),
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
- 결론을 먼저 정하고 근거를 끼워 맞추지 않는다. 근거가 얼마나 강한지는 확신도(confidence)로 솔직하게 나타낸다.
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

def decision_guide(practice: bool = False) -> str:
    """최종 결정 기준. 예전엔 '근거가 약하면 Hold' 만 있어서 숫자 거르기를 통과한 후보도 거의 다 보류로 끝났다.
    매수가 곧 큰 위험은 아니다: 손절선·하루 손실 한도·숫자 체크리스트가 뒤에서 한 번 더 막는다."""
    g = ("등급 기준: 회의 자료의 숫자(추세·모멘텀·거래대금)가 양호하고 아래 위험 신호가 없으면 "
         "Overweight(계획 금액의 일부로 작게 시작, 손절선 있음)를 기본으로 한다. 근거가 여러 면에서 강하면 Buy. "
         "Hold 는 구체적인 위험 신호가 있을 때만 고른다: 실적 쇼크·가이던스 하향, 투자 주의·경고 등 공시 위험, "
         "며칠 사이 급등으로 과열, 팩트체크에서 '틀림'이 나온 핵심 주장, 곧 있을 큰 이벤트(실적 발표 등)로 방향을 알 수 없음. "
         "웹에서 새 소식을 찾지 못했다는 것만으로는 Hold 이유가 되지 않는다 (숫자 자료도 근거다). "
         "Hold 를 고르면 headline 에 그 위험 신호를 적는다. 확신이 낮으면 등급을 낮추기보다 confidence 를 낮춰라.")
    if practice:
        g += (" 지금은 가상매매 연습 중이다 (실제 돈이 아님). 매수·손절·익절이 제대로 도는지 확인하는 단계라, "
              "위험 신호가 없으면 Overweight 로 작게 시작하는 쪽을 택한다.")
    return g


PROFILE_DEFAULT = """- 개별 주식으로 장기적으로 시장 지수(코스피, S&P500) 수익률을 넘는 초과 수익을 노린다.
- 레버리지(빚투), 공매도, 옵션은 하지 않는다.
- 무리한 위험은 피한다. 목표를 못 이루더라도 원금을 크게 잃는 것은 원하지 않는다.
- 크게 성장할 잠재력(텐배거)이 있는 종목에 관심이 많지만, 냉철하고 현실적인 판단을 원한다.
- 내 생각이 틀렸다면 듣기 불편하더라도 필터링 없이 솔직하게 말해 주길 원한다.
- 투자 경험이 길지 않아 어려운 용어는 쉽게 풀어서 설명해 주길 원한다."""

ROLES: list[Role] = [
    Role("chart", "차트봇", "기술 분석가", "analysts", False, False, "teal",
         "가격 추세, 이동평균 배열, RSI, 볼린저밴드, ATR 변동성, 거래량, 52주 위치로 기술적 국면을 판단한다. "
         "지지선·저항선 가격대와 추세가 깨지는 가격을 구체적으로 제시하고, 지금이 사기 좋은 자리(눌림·돌파)인지 "
         "이미 많이 오른 자리인지 말한다."),
    Role("fund", "밸류", "재무·실적 분석가", "analysts", True, False, "navy",
         "웹 검색(공시·IR 우선)으로 정리한다: 사업과 매출 구성, 최근 분기 매출·영업이익·순이익과 마진 추이(표), "
         "재무상태(부채·현금), 최근 실적 발표의 컨센서스 대비 서프라이즈와 가이던스 변화, 경영진이 약속한 것과 실제 실행. "
         "밸류에이션은 PER·선행 PER·PBR·EV/EBITDA 를 '현재 / 과거 평균 / 경쟁사' 로 비교해 비싼지 싼지 판단한다."),
    Role("news", "헤드라인", "뉴스·수급 분석가", "analysts", True, False, "amber",
         "웹 검색으로 최근 2주 종목·업종 뉴스와 공시를 호재/악재로 나누고 주가 영향 크기(상/중/하)를 매긴다. "
         "예정된 이벤트(실적 발표일, 규제, 금리 결정)와 날짜를 표로 정리한다. 투자자별 순매수(국내는 회의 자료의 숫자), "
         "공매도·신용 동향, 기관·내부자 매매 소식으로 수급을 해석한다."),
    Role("checker", "검증관", "팩트체커", "check", True, False, "white",
         "분석가 보고서의 핵심 수치와 주장을 공시·IR·신뢰할 수 있는 자료로 다시 확인한다. 확인됨/확인 불가/틀림으로 판정한다. "
         "'확인 불가'는 전망처럼 아직 확인할 수 없는 것에만 쓴다. 그리고 투자 전에 꼭 알아야 할 질문 3가지를 뽑아 답한다. "
         "밸류에이션 지표와 분야별 점수도 정리한다. 결과는 지정된 JSON 형식으로만 낸다."),
    Role("bull", "불리", "강세 연구원", "research", False, False, "green",
         "보고서와 팩트체크 결과를 근거로 매수 논리(Bull Case)를 핵심 근거 3가지로 세운다. "
         "팩트체크에서 '틀림'으로 나온 주장은 근거로 쓰지 않는다."),
    Role("bear", "베어리", "약세 연구원", "research", False, False, "red",
         "보고서와 팩트체크 결과에서 '사면 안 되는 이유'를 찾는다: 실적 쇼크·가이던스 하향, 투자 주의·경고 등 공시 위험, "
         "며칠 사이 급등으로 과열, 곧 있을 큰 이벤트, 틀린 핵심 주장. 찾지 못했으면 '뚜렷한 위험 신호 없음'이라고 솔직히 쓴다."),
    Role("pm", "대표", "대표 (최종 결정)", "pm", False, False, "gold",
         "모든 보고와 토론을 보고 최종 결정을 내린다. 리서치 팀장(투자 논문), 트레이더(진입가·손절·익절), "
         "리스크 점검(손실 가능성·이벤트 위험·투자자 프로필) 역할을 함께 맡는다. 결과는 지정된 JSON 형식으로만 낸다."),
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
    "key_questions": {"type": "array", "description": "투자 전에 꼭 알아야 할 질문 3가지",
                      "items": _obj({"question": STR, "answer": STR, "source": STR})},
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
    "risk_check": _obj({"verdict": {"type": "string", "enum": ["확대", "유지", "축소", "관망"]},
                        "note": {"type": "string", "description": "손실 가능성·이벤트 위험·투자자 프로필을 따진 한두 문장"}}),
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
