"""설정 파일과 데이터 폴더.

데이터 폴더는 QTRADER_HOME 환경변수, 없으면 %APPDATA%\\QTrader (윈도우) 또는 ~/.qtrader.
API 키는 이 파일에 넣지 않는다. secrets.py 가 OS 자격 증명 저장소에 보관한다.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

MODES = ("paper", "kis_demo", "kis_real")

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
    "kis": {"account": ""},  # "12345678-01"
    "paper": {"initial_cash": {"KR": 10_000_000, "US": 10_000}},
    "history_days": 400,
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
    if cfg.get("mode") != "paper" and not cfg.get("kis", {}).get("account"):
        errs.append("KIS 모드에는 kis.account(예: 12345678-01)가 필요합니다")
    return errs


def save(cfg: dict) -> None:
    path().write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
