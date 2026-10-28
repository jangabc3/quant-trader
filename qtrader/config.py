"""설정 파일과 데이터 폴더.

데이터 폴더는 QTRADER_HOME 환경변수, 없으면 %APPDATA%\\QTrader (윈도우) 또는 ~/.qtrader.
API 키는 이 파일에 넣지 않는다. secrets.py 가 OS 자격 증명 저장소에 보관한다.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

MODES = ("paper", "toss_real", "kis_demo", "kis_real")
LIVE_MODES = ("toss_real", "kis_real")
DATA_SOURCES = ("auto", "toss", "kis", "csv")

DEFAULT = {
    "mode": "paper",
    "migrated": ["all_sessions", "meetings_per_market"],  # 이미 적용한 기본값 바꾸기 (migrate)
    # 실전 주문은 mode=kis_real 이면서 이 값이 true 일 때만 나간다
    "live_confirmed": False,
    "markets": {
        "KR": {
            "enabled": True,
            "strategy": "ai",
            "params": {"lookback": 120, "skip": 5, "top_k": 3, "rebalance_days": 20},
            # 예시 종목: 삼성전자, SK하이닉스, NAVER, 현대차, LG에너지솔루션, 카카오, 셀트리온, KB금융
            "universe": ["005930", "000660", "035420", "005380", "373220", "035720", "068270", "105560"],
            "run_time": "15:10",  # KST. 장 마감(15:30) 전
            # 넥스트레이드(NXT) 08:00~20:00 에도 감시·사고팔기 (지정가만). 정규장(09:00~15:30)만 하려면 끈다
            "extended": True,
            "budget": 10_000_000,  # 이 전략이 쓸 최대 금액(원)
            # AI 회의를 국내·미국 따로: 켜기/끄기, 하루에 회의할 종목 수 (비용 상한. 회의 1번 = Claude 7번 + 웹 검색)
            "ai_meetings": True,
            "meetings_per_day": 4,
        },
        "US": {
            "enabled": False,
            "strategy": "ai",
            "params": {"fast": 20, "slow": 60},
            # "AAPL" 은 나스닥으로 본다. 다른 거래소는 "NYSE:KO", "AMEX:SPY" 처럼 쓴다
            "universe": ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "AMEX:SPY", "NYSE:KO"],
            "run_time": "15:45",  # 뉴욕 시간. 장 마감(16:00) 전
            "budget": 10_000,  # 달러
            "ai_meetings": True,
            "meetings_per_day": 4,
            "limit_offset": 0.003,  # 미국 지정가 주문을 현재가에서 얼마나 유리하게 걸지
            # 정기 실행: regular = 뉴욕 정규장 마감 전 (run_time 은 뉴욕 시각), day = 한국 낮 주간거래 (run_time 은 한국 시각)
            "run_session": "regular",
            # 정규장 밖에서도 감시·사고팔 세션: day(주간거래, 한국 낮), pre(프리마켓), after(애프터마켓). 모두 지정가만
            "extended_sessions": ["day", "pre", "after"],
            "day_hours": "10:00-17:30",  # 주간거래 시간 (한국 시각). 토스 앱의 실제 시간에 맞게
        },
    },
    "risk": {
        "max_weight": 0.20,
        "max_exposure": 0.95,
        "max_order_value": {"KR": 3_000_000, "US": 3_000},
        "daily_loss_limit": 0.03,
    },
    # 시세: auto 는 토스증권 키 > KIS 키 > CSV 순으로 고른다
    "data_source": "auto",
    "toss": {
        "account_seq": None,  # 비우면 첫 종합매매 계좌
        "us_loc": True,  # 미국 주문을 LOC(장 마감 지정가)로
        # 손절·익절을 토스 서버 OCO 조건주문으로도 걸어 둔다 (PC 가 꺼져도 토스가 판다)
        "server_guard": True,
    },
    "kis": {"account": ""},  # "12345678-01"
    "paper": {"initial_cash": {"KR": 10_000_000, "US": 10_000}},
    "history_days": 400,
    # AI 투자 회의 (TradingAgents 구조). 전략을 "ai" 로 고른 시장에서 주문 근거가 된다
    "ai": {
        # 분석가·토론자는 빠른 모델, 검증관·팀장·대표는 깊은 모델 (TradingAgents 의 quick/deep 구분,
        # 타민더마켓 가이드의 'Sonnet 기본 + 복잡한 분석만 Opus' 토큰 절약 팁)
        "quick_model": "claude-sonnet-5-5",
        "chat_effort": "medium",  # 큐비 답변 깊이 (low 는 빠르고 싸지만 분석이 얕아짐)
        "deep_model": "claude-opus-5-5",
        "effort": "medium",
        # fast: Claude 호출 7번 (분석 묶음 + 대표가 팀장·트레이더·리스크 역할 통합), precise: 14번
        "mode": "fast",
        # 매수 체크리스트 기준 (principles.DEFAULT_RULES 를 덮어쓴다)
        "rules": {},
        # AI 로 산 종목은 손절가·익절가에 닿으면 장중에 자동으로 판다
        "auto_exit": True,
        # 모든 참석자가 읽는 투자자 프로필 (가이드 STEP 0 '프로젝트 기본 지령'). 비우면 기본 프로필
        "profile": "",
        # AI 회의로 정한 주문은 화면에서 승인해야 나간다 (가이드 원칙: 최종 판단은 사람이)
        "require_approval": False,
        "debate_rounds": 1,
        "risk_rounds": 1,
        "web_research": True,  # 뉴스·재무·여론 분석가가 웹 검색을 쓴다
        "run_before_minutes": 40,  # 실행 시각보다 이만큼 먼저 회의를 돌린다
        # 최종 등급 -> 목표 비중 배수 (종목당 1/N 기준). Hold(보류)는 새로 사지 않고 가진 종목은 그대로
        "rating_weights": {"Buy": 1.0, "Overweight": 0.7, "Hold": None, "Underweight": 0.3, "Sell": 0.0},
        "min_confidence": 0.5,  # 이보다 확신이 낮으면 Hold 로 본다
        "monthly_cost_cap_krw": 30000,  # 이번 달 AI 회의 비용이 이만큼 넘으면 새 회의를 열지 않는다 (0 = 제한 없음)
        # 회의할 종목을 AI 가 매일 고른다 (설정 종목 + 관심 종목 + 보유 + 거래대금 상위 중 숫자 규칙 통과)
        "auto_pick": True,
        # 매매 기간: auto(AI가 종목마다) / long(장기) / swing(스윙, 며칠~2주) / short(단기 1~3일). 단기는 손절·익절이 좁고 기한이 지나면 판다
        "holding": "auto",
        # 장중 AI 긴급 재점검: 가진 종목이 급락하거나 손절선에 가까울 때만, 하루 이만큼까지 (위험을 줄이는 매도만 바로 한다)
        "live_recheck": True,
        "recheck_per_day": 5,  # 하루 재점검 횟수 상한 (0 = 재점검 안 함)
        "recheck_every_min": 2,  # 재점검이 필요한지 몇 분마다 볼지
        # 장중 계속 사기: 장중 N분마다 새로 규칙을 통과한 후보로 회의를 열고, 결론이 매수이고 체크리스트를 통과하면 그때 바로 산다.
        # 끄면 하루 한 번 정기 실행 때만 산다. 회의 수는 시장별 meetings_per_day·monthly_cost_cap_krw 에 함께 센다
        "intraday": True,
        "intraday_every_min": 15,
    },
    # 알림: 화면(윈도우 알림)은 항상, 텔레그램은 봇 토큰(키 보관함)과 대화방 번호가 있을 때
    "notify": {"telegram_chat_id": "", "daily_report": True},
    "alerts": [],  # 가격 알림 [{symbol, name, op: above|below, price}]
    "max_sector_weight": 0.4,  # 한 업종 비중 경고 기준
    # 자동 업데이트: GitHub 저장소 "아이디/저장소이름" (Releases 에 -win.zip 과 .sha256 을 올린다). 비우면 확인 안 함
    "update": {"repo": "", "check": True},
    # 미국 주식은 달러로만 산다. 달러가 모자라면 자동 환전하지 않고 알려서 환전 우대로 직접 바꾸게 한다
    "fx_policy": "preferential_only",
    # 전략 비교: 같은 회의 결과로 매매 기간만 다른 가상 계좌들을 함께 돌린다 (AI 비용 추가 없음)
    "compare": {"enabled": False, "modes": ["auto", "long", "swing", "short"]},
    "watchlist": ["005930", "000660", "AAPL", "NVDA"],
    "investor": None,  # 투자 성향 검사 결과 (처음 실행 때 묻는다)
    "news": {"feeds": [
        {"name": "한국경제 증권", "url": "https://www.hankyung.com/feed/finance", "cat": "kr"},
        {"name": "매일경제 증권", "url": "https://www.mk.co.kr/rss/50200011/", "cat": "kr"},
        {"name": "연합뉴스 마켓", "url": "https://www.yna.co.kr/rss/market.xml", "cat": "kr"},
        {"name": "머니투데이 증권", "url": "https://rss.mt.co.kr/mt_news_stock.xml", "cat": "kr"},
        {"name": "조선비즈 증권", "url": "https://biz.chosun.com/arc/outboundfeeds/rss/category/stock/?outputType=xml", "cat": "kr"},
        {"name": "파이낸셜뉴스 증권", "url": "https://www.fnnews.com/rss/r20/fn_realnews_stock.xml", "cat": "kr"},
        {"name": "인베스팅닷컴", "url": "https://kr.investing.com/rss/news_25.rss", "cat": "global"},
        {"name": "인베스팅닷컴 주식", "url": "https://kr.investing.com/rss/stock.rss", "cat": "global"},
        {"name": "조선비즈 국제", "url": "https://biz.chosun.com/arc/outboundfeeds/rss/category/international/?outputType=xml", "cat": "global", "filter": True},
        {"name": "한국경제 국제", "url": "https://www.hankyung.com/feed/international", "cat": "global", "filter": True},
        {"name": "매일경제 국제", "url": "https://www.mk.co.kr/rss/30300018/", "cat": "global", "filter": True},
        {"name": "뉴욕증시·나스닥", "url": "https://news.google.com/rss/search?hl=ko&gl=KR&ceid=KR:ko&q=%EB%89%B4%EC%9A%95%EC%A6%9D%EC%8B%9C%20OR%20%EB%82%98%EC%8A%A4%EB%8B%A5%20%EB%A7%88%EA%B0%90%20OR%20%EB%8B%A4%EC%9A%B0%EC%A7%80%EC%88%98%20OR%20S%26P500%20when%3A2d", "cat": "global"},
        {"name": "월가·연준", "url": "https://news.google.com/rss/search?hl=ko&gl=KR&ceid=KR:ko&q=%EC%9B%94%EA%B0%80%20OR%20%EC%97%B0%EC%A4%80%20OR%20%ED%8C%8C%EC%9B%94%20OR%20%EB%AF%B8%EA%B5%AD%20%EA%B5%AD%EC%B1%84%20when%3A2d", "cat": "global"},
        {"name": "미국 빅테크", "url": "https://news.google.com/rss/search?hl=ko&gl=KR&ceid=KR:ko&q=%EC%97%94%EB%B9%84%EB%94%94%EC%95%84%20OR%20%ED%85%8C%EC%8A%AC%EB%9D%BC%20OR%20%EC%95%A0%ED%94%8C%20OR%20%EB%A7%88%EC%9D%B4%ED%81%AC%EB%A1%9C%EC%86%8C%ED%94%84%ED%8A%B8%20OR%20%ED%8C%94%EB%9E%80%ED%8B%B0%EC%96%B4%20%EC%A3%BC%EA%B0%80%20when%3A2d", "cat": "global"},
        {"name": "서학개미·해외주식", "url": "https://news.google.com/rss/search?hl=ko&gl=KR&ceid=KR:ko&q=%EC%84%9C%ED%95%99%EA%B0%9C%EB%AF%B8%20OR%20%ED%95%B4%EC%99%B8%EC%A3%BC%EC%8B%9D%20OR%20%EB%AF%B8%EA%B5%AD%20ETF%20when%3A3d", "cat": "global"},
    ]},
    # 미국 재무제표(SEC EDGAR)는 요청에 연락처가 필요하다
    "sec_contact": "",
}


def home() -> Path:
    env = os.environ.get("QTRADER_HOME")
    if env:
        p = Path(env)
    elif os.name == "nt" and os.environ.get("APPDATA"):
        p = Path(os.environ["APPDATA"]) / "QTrader"
    else:
        p = Path.home() / ".qtrader"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def path() -> Path:
    return home() / "config.json"


def load() -> dict:
    p = path()
    if not p.exists():
        save(DEFAULT)
        return copy.deepcopy(DEFAULT)
    saved = json.loads(p.read_text(encoding="utf-8"))
    cfg = migrate(_merge(DEFAULT, saved), saved)
    # 예전 기본 뉴스 피드(인베스팅닷컴 2개)만 저장돼 있으면 새 기본 목록으로 바꾼다
    old = {"https://kr.investing.com/rss/news_25.rss", "https://kr.investing.com/rss/news.rss"}
    feeds = cfg.get("news", {}).get("feeds") or []
    english = {"https://www.cnbc.com/id/100003114/device/rss/rss.html", "https://feeds.content.dowjones.io/public/rss/mw_topstories"}
    names = {f.get("name") for f in feeds}
    legacy = {"뉴욕증시", "미국증시·나스닥"} & names and "월가·연준" not in names
    if feeds and ({f.get("url") for f in feeds} <= old or legacy or any(("cnbc.com" in (f.get("url") or "")) or ("dowjones" in (f.get("url") or "")) or f.get("url") in english for f in feeds)
                  or not all("cat" in f for f in feeds)):
        cfg["news"]["feeds"] = copy.deepcopy(DEFAULT["news"]["feeds"])
    return cfg


# 한 번만 적용하는 기본값 바꾸기. 예전에 저장한 설정에는 옛 기본값이 그대로 들어 있어서, 새 기본값을 한 번 덮어쓴다
# (적용한 뒤 사용자가 끄고 저장하면 그 선택을 따른다)
MIGRATIONS = ("all_sessions", "meetings_per_market")


def migrate(cfg: dict, saved: dict) -> dict:
    """saved 는 파일에 저장돼 있던 그대로 (기본값과 합치기 전). 합친 뒤에는 새 기본값의 표시가 섞이기 때문."""
    done = set(saved.get("migrated") or [])
    if "all_sessions" not in done:  # 프리장·장중·애프터장 내내 사고팔기 (사용자 요청 2026-10)
        cfg["markets"]["KR"]["extended"] = True
        cfg["markets"]["US"]["extended_sessions"] = ["day", "pre", "after"]
    if "meetings_per_market" not in done:  # 하루 회의 수를 국내·미국 따로 (예전 하나로 합친 값은 반씩 나눈다)
        old = (saved.get("ai") or {}).get("max_symbols_per_day")
        if isinstance(old, int) and not any("meetings_per_day" in (saved.get("markets") or {}).get(m, {}) for m in ("KR", "US")):
            for m in ("KR", "US"):
                cfg["markets"][m]["meetings_per_day"] = max(1, old // 2)
    cfg.get("ai", {}).pop("max_symbols_per_day", None)
    cfg["migrated"] = sorted(done | set(MIGRATIONS))
    return cfg


def validate(cfg: dict) -> list[str]:
    errs = []
    if cfg.get("mode") not in MODES:
        errs.append(f"mode 는 {MODES} 중 하나여야 합니다")
    r = cfg.get("risk", {})
    if not 0 < r.get("max_weight", 0) <= 1:
        errs.append("risk.max_weight 는 0~1 사이여야 합니다")
    if not 0 < r.get("max_exposure", 0) <= 1:
        errs.append("risk.max_exposure 는 0~1 사이여야 합니다")
    if not 0 < r.get("daily_loss_limit", 0) < 1:
        errs.append("risk.daily_loss_limit 는 0~1 사이여야 합니다")
    for m, mc in cfg.get("markets", {}).items():
        if m not in ("KR", "US"):
            errs.append(f"알 수 없는 시장: {m}")
            continue
        hh, _, mm = str(mc.get("run_time", "")).partition(":")
        if not (hh.isdigit() and mm.isdigit() and int(hh) < 24 and int(mm) < 60):
            errs.append(f"{m}.run_time 은 HH:MM 형식이어야 합니다")
        if mc.get("budget", 0) <= 0:
            errs.append(f"{m}.budget 은 0보다 커야 합니다")
    if str(cfg.get("mode", "")).startswith("kis") and not cfg.get("kis", {}).get("account"):
        errs.append("KIS 모드에는 kis.account(예: 12345678-01)가 필요합니다")
    if cfg.get("data_source", "auto") not in DATA_SOURCES:
        errs.append(f"data_source 는 {DATA_SOURCES} 중 하나여야 합니다")
    ai = cfg.get("ai", {})
    if ai.get("effort") not in ("low", "medium", "high", "xhigh", "max"):
        errs.append("ai.effort 는 low/medium/high/xhigh/max 중 하나여야 합니다")
    if ai.get("mode", "fast") not in ("fast", "precise"):
        errs.append("ai.mode 는 fast 또는 precise")
    if not 0 <= int(ai.get("debate_rounds", 1)) <= 3 or not 0 <= int(ai.get("risk_rounds", 1)) <= 3:
        errs.append("토론 라운드는 0~3 이어야 합니다")
    if not isinstance(ai.get("intraday", True), bool):
        errs.append("ai.intraday 는 true 또는 false")

    def _int_in(k, lo, hi, default):
        v = ai.get(k, default)
        return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi
    if not _int_in("intraday_every_min", 5, 240, 15):
        errs.append("장중 회의 간격(ai.intraday_every_min)은 5~240분이어야 합니다")
    if not _int_in("recheck_every_min", 2, 240, 2):
        errs.append("재점검 간격(ai.recheck_every_min)은 2~240분이어야 합니다")
    if not _int_in("recheck_per_day", 0, 50, 5):
        errs.append("하루 재점검 횟수(ai.recheck_per_day)는 0~50 이어야 합니다")
    for m, mc in (cfg.get("markets") or {}).items():
        v = mc.get("meetings_per_day", 4)
        if not (isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 50):
            errs.append(f"{m} 하루 회의 종목 수(meetings_per_day)는 0~50 이어야 합니다")
        if not isinstance(mc.get("ai_meetings", True), bool):
            errs.append(f"{m}.ai_meetings 는 true 또는 false")
    return errs


def meetings_cap(cfg: dict, market) -> int:
    """이 시장에서 하루에 AI 회의를 열 수 있는 종목 수. 이 시장 AI 회의를 끄면 0."""
    mc = cfg["markets"][getattr(market, "value", market)]
    return int(mc.get("meetings_per_day", 4)) if mc.get("ai_meetings", True) else 0


def met_today(store, market, today: str) -> set[str]:
    """오늘 이 시장에서 AI 회의를 한 종목 (정기·장중·직접 연 회의 모두)."""
    mv = getattr(market, "value", market)
    return {r["symbol"] for r in store.ai_runs(300) if r["trade_date"] == today and r["market"] == mv}


def save(cfg: dict) -> None:
    path().write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
