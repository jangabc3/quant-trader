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
    # 실전 주문은 mode=kis_real 이면서 이 값이 true 일 때만 나간다
    "live_confirmed": False,
    "markets": {
        "KR": {
            "enabled": True,
            "strategy": "momentum",
            "params": {"lookback": 120, "skip": 5, "top_k": 3, "rebalance_days": 20},
            # 예시 종목: 삼성전자, SK하이닉스, NAVER, 현대차, LG에너지솔루션, 카카오, 셀트리온, KB금융
            "universe": ["005930", "000660", "035420", "005380", "373220", "035720", "068270", "105560"],
            "run_time": "15:10",  # KST. 장 마감(15:30) 전
            "budget": 10_000_000,  # 이 전략이 쓸 최대 금액(원)
        },
        "US": {
            "enabled": False,
            "strategy": "trend",
            "params": {"fast": 20, "slow": 60},
            # "AAPL" 은 나스닥으로 본다. 다른 거래소는 "NYSE:KO", "AMEX:SPY" 처럼 쓴다
            "universe": ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "AMEX:SPY", "NYSE:KO"],
            "run_time": "15:45",  # 뉴욕 시간. 장 마감(16:00) 전
            "budget": 10_000,  # 달러
            "limit_offset": 0.003,  # 미국 지정가 주문을 현재가에서 얼마나 유리하게 걸지
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
    },
    "kis": {"account": ""},  # "12345678-01"
    "paper": {"initial_cash": {"KR": 10_000_000, "US": 10_000}},
    "history_days": 400,
    # AI 투자 회의 (TradingAgents 구조). 전략을 "ai" 로 고른 시장에서 주문 근거가 된다
    "ai": {
        "model": "claude-opus-5-5",
        "effort": "medium",
        "debate_rounds": 1,
        "risk_rounds": 1,
        "web_research": True,  # 뉴스·재무·여론 분석가가 웹 검색을 쓴다
        "run_before_minutes": 40,  # 실행 시각보다 이만큼 먼저 회의를 돌린다
        "max_symbols_per_day": 8,  # 비용 상한: 하루 회의 종목 수
        # 최종 등급 -> 목표 비중 배수 (종목당 1/N 기준). Hold 는 보유 유지
        "rating_weights": {"Buy": 1.0, "Overweight": 0.7, "Hold": None, "Underweight": 0.3, "Sell": 0.0},
        "min_confidence": 0.5,  # 이보다 확신이 낮으면 Hold 로 본다
    },
    "watchlist": ["005930", "000660", "AAPL", "NVDA"],
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
    return _merge(DEFAULT, json.loads(p.read_text(encoding="utf-8")))


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
    if not 0 <= int(ai.get("debate_rounds", 1)) <= 3 or not 0 <= int(ai.get("risk_rounds", 1)) <= 3:
        errs.append("토론 라운드는 0~3 이어야 합니다")
    return errs


def save(cfg: dict) -> None:
    path().write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
