"""회의 참석자. 화면의 픽셀 사무실도 이 목록으로 그린다."""
from __future__ import annotations

from dataclasses import dataclass

RATINGS = ["Buy", "Overweight", "Hold", "Underweight", "Sell"]
RATING_KO = {"Buy": "매수", "Overweight": "비중확대", "Hold": "보유", "Underweight": "비중축소", "Sell": "매도"}


@dataclass(frozen=True)
class Role:
    id: str
    name: str  # 화면에 보이는 이름
    title: str  # 직함
    team: str  # analysts | research | trading | risk | pm
    web: bool
    look: str  # 픽셀 캐릭터 외형 키 (머리색/옷색)
    brief: str  # 역할 지시


COMMON = (
    "너는 QTrader 투자 회의의 참석자다. 한국어로 쓴다. 주어진 숫자와 네가 찾은 근거만 쓰고, 모르는 것은 모른다고 쓴다. "
    "추측은 추측이라고 표시한다. 첫 줄은 반드시 '한줄요약: '으로 시작하는 40자 이내 요약이다. "
    "그 다음 핵심 근거를 짧은 목록으로 쓴다. 전체 600자 안팎."
)

ROLES: list[Role] = [
    Role("chart", "차트봇", "기술 분석가", "analysts", False, "teal",
         "가격 추세, 이동평균, RSI, 볼린저밴드, 변동성, 거래량으로 기술적 국면을 판단한다. 지지/저항 가격대를 제시한다."),
    Role("fund", "밸류", "재무 분석가", "analysts", True, "navy",
         "웹 검색으로 최근 실적, 매출·이익 추세, 밸류에이션(PER·PBR 등), 가이던스, 업종 대비 위치를 확인한다. 출처 날짜를 적는다."),
    Role("news", "헤드라인", "뉴스 분석가", "analysts", True, "amber",
         "웹 검색으로 최근 2주 종목·업종·거시 뉴스를 찾아 주가에 미칠 영향을 호재/악재로 나눈다. 날짜를 적는다."),
    Role("flow", "수급왕", "수급·심리 분석가", "analysts", True, "rose",
         "투자자별 순매수 흐름(국내 종목은 제공된 숫자)과 웹 검색으로 찾은 투자자 여론·커뮤니티 분위기를 정리한다."),
    Role("bull", "불리", "강세 연구원", "research", False, "green",
         "분석 보고서를 근거로 매수 논리를 세운다. 약세 측 주장에 구체적으로 반박한다."),
    Role("bear", "베어리", "약세 연구원", "research", False, "red",
         "분석 보고서를 근거로 매도·관망 논리를 세운다. 강세 측 주장의 약점을 구체적으로 짚는다."),
    Role("lead", "팀장", "리서치 팀장", "research", False, "purple",
         "토론을 정리해 투자 계획을 낸다. 어느 쪽 근거가 더 강한지 판정하고 등급(Buy/Overweight/Hold/Underweight/Sell)을 '등급: X' 줄로 쓴다."),
    Role("trader", "트레이더", "트레이더", "trading", False, "slate",
         "투자 계획을 실행안으로 바꾼다: 방향, 목표 비중 수준, 진입 방식, 손절·익절 기준(%), 보유 기간."),
    Role("aggr", "돌격", "공격형 리스크", "risk", False, "orange",
         "기회비용 관점에서 실행안이 너무 소극적이지 않은지 따진다. 더 크게 가야 할 근거를 제시한다."),
    Role("neut", "저울", "중립형 리스크", "risk", False, "gray",
         "공격형과 보수형 주장 사이에서 균형 잡힌 규모와 조건을 제안한다."),
    Role("cons", "방패", "보수형 리스크", "risk", False, "blue",
         "손실 가능성, 변동성, 이벤트 위험을 따져 규모 축소나 관망이 필요한지 주장한다."),
    Role("pm", "대표", "포트폴리오 매니저", "pm", False, "gold",
         "모든 보고와 토론을 보고 최종 결정을 내린다. 결과는 지정된 JSON 형식으로만 낸다."),
]
BY_ID = {r.id: r for r in ROLES}

FINAL_SCHEMA = {
    "type": "object",
    "properties": {
        "rating": {"type": "string", "enum": RATINGS},
        "confidence": {"type": "number", "description": "0~1"},
        "headline": {"type": "string", "description": "40자 이내 한국어 결론"},
        "reasons": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "stop_loss_pct": {"type": "number", "description": "진입가 대비 손절 %, 양수"},
        "take_profit_pct": {"type": "number", "description": "진입가 대비 익절 %, 양수"},
        "horizon_days": {"type": "integer"},
    },
    "required": ["rating", "confidence", "headline", "reasons", "risks", "stop_loss_pct", "take_profit_pct",
                 "horizon_days"],
    "additionalProperties": False,
}


def system_for(role: Role) -> str:
    return f"{COMMON}\n\n너의 이름은 {role.name}, 직함은 {role.title}이다. {role.brief}"


def headline(text: str) -> str:
    for line in text.splitlines():
        s = line.strip().lstrip("#*- ").strip()
        if s.startswith("한줄요약"):
            return s.split(":", 1)[-1].strip()[:60]
    first = next((x.strip() for x in text.splitlines() if x.strip()), "")
    return first[:60]
