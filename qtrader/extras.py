"""알림, 하루 요약, 가격 알림, 실현 손익·세금, 업종 쏠림, AI 비용, 절전 방지, 윈도우 시작 시 자동 실행."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import requests

from . import vault

KRW_PER_USD = 1400  # 환율을 못 받을 때 쓰는 대략값
US_TAX_FREE_KRW = 2_500_000  # 해외주식 양도소득 기본공제 (연)
US_TAX_RATE = 0.22  # 양도소득세 20% + 지방소득세 2%


# 알림: 기록('notice' 등급은 화면이 윈도우 알림으로 띄운다) + 텔레그램(설정했을 때)
def notify(store, cfg: dict, text: str, source: str = "notify") -> None:
    store.log("notice", source, text)
    tok, chat = vault.get("telegram_bot_token"), (cfg.get("notify") or {}).get("telegram_chat_id")
    if tok and chat:
        try:
            requests.post(f"https://api.telegram.org/bot{tok}/sendMessage", timeout=8,
                          json={"chat_id": chat, "text": f"[QTrader] {text}"})
        except requests.RequestException as e:
            store.log("warn", "notify", f"텔레그램 전송 실패: {type(e).__name__}")


# 실현 손익: 체결된 주문을 평균단가 방식으로 따라가며 매도할 때의 이익을 더한다
def realized(store, account: str, market: str, since: str = "") -> dict:
    rows = store.rows("select * from orders where account=? and market=? and status='filled' and trade_date>=? "
                      "order by id", (account, market, since))
    book: dict[str, list[float]] = {}
    total, by_symbol, trades, wins = 0.0, {}, 0, 0
    for o in rows:
        q, avg = book.get(o["symbol"], [0, 0.0])
        if o["side"] == "buy":
            nq = q + o["qty"]
            book[o["symbol"]] = [nq, (q * avg + o["qty"] * o["price"]) / nq if nq else 0]
        elif q > 0:
            sold = min(o["qty"], q)
            pnl = sold * (o["price"] - avg)
            total += pnl
            by_symbol[o["symbol"]] = by_symbol.get(o["symbol"], 0) + pnl
            trades += 1
            wins += pnl > 0
            book[o["symbol"]] = [q - sold, avg]
    return {"total": total, "by_symbol": by_symbol, "trades": trades, "win_rate": wins / trades if trades else None}


def tax_summary(store, account: str, year: str, fx: float | None = None) -> dict:
    """올해 실현 손익과 세금 추정. 국내 상장주식은 소액주주 양도세가 없고 거래세는 수수료에 포함된다."""
    kr = realized(store, account, "KR", f"{year}-01-01")
    us = realized(store, account, "US", f"{year}-01-01")
    rate = fx or KRW_PER_USD
    us_krw = us["total"] * rate
    taxable = max(0.0, us_krw - US_TAX_FREE_KRW)
    return {"year": year, "kr": kr, "us": us, "fx": rate, "us_krw": us_krw, "us_taxable_krw": taxable,
            "us_tax_krw": taxable * US_TAX_RATE,
            "note": "추정치입니다. 해외주식은 연 250만원 공제 뒤 22%를 다음 해 5월에 직접 신고합니다. "
                    "국내 상장주식은 대주주가 아니면 양도세가 없습니다. 같은 해 다른 증권사 손익과 합산해야 합니다."}


# 업종 쏠림: 자주 쓰는 종목의 업종표 (모르는 종목은 '기타')
SECTORS = {
    "반도체": ["005930", "000660", "042700", "NVDA", "AMD", "AVGO", "TSM", "INTC", "QCOM", "MU", "ASML"],
    "빅테크·인터넷": ["AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "NFLX", "035420", "035720"],
    "2차전지": ["373220", "006400", "051910", "247540", "086520", "003670"],
    "자동차": ["005380", "000270", "012330", "TSLA", "GM", "F"],
    "바이오·헬스케어": ["207940", "068270", "196170", "LLY", "JNJ", "UNH", "PFE", "MRK"],
    "금융": ["105560", "055550", "086790", "JPM", "BAC", "GS", "V", "MA"],
}
SECTOR_OF = {s: k for k, v in SECTORS.items() for s in v}


def concentration(holdings: list[dict], max_weight: float, max_sector: float = 0.4, cash: float = 0.0) -> dict:
    """holdings: [{symbol, value}] (같은 통화). 비중은 현금까지 더한 총자산 기준. 한 종목·한 업종이 기준을 넘으면 경고."""
    total = (sum(h["value"] for h in holdings) + max(cash, 0)) if holdings else 0
    if not total:
        return {"sectors": [], "warnings": []}
    sec: dict[str, float] = {}
    warns = []
    for h in holdings:
        w = h["value"] / total
        sec[SECTOR_OF.get(h["symbol"], "기타")] = sec.get(SECTOR_OF.get(h["symbol"], "기타"), 0) + w
        if w > max_weight + 1e-9:
            warns.append(f"{h.get('name') or h['symbol']} 비중 {w:.0%} 가 한 종목 상한 {max_weight:.0%} 를 넘습니다")
    for k, w in sec.items():
        if k != "기타" and w > max_sector:
            warns.append(f"{k} 업종에 {w:.0%} 가 몰려 있습니다 (권장 {max_sector:.0%} 이하). 한 업종 악재에 같이 흔들립니다")
    return {"sectors": sorted(({"name": k, "weight": w} for k, w in sec.items()), key=lambda x: -x["weight"]),
            "warnings": warns}


# 하루 요약
def daily_report(store, account: str, market: str, today: str) -> dict:
    eq = store.equity_history(account, market)
    last = eq[-1]["equity"] if eq else None
    prev = eq[-2]["equity"] if len(eq) > 1 else None
    orders = store.rows("select * from orders where account=? and market=? and trade_date=? and status in "
                        "('filled','submitted') order by id", (account, market, today))
    plans = store.active_plans(account, market)
    year = realized(store, account, market, today[:4] + "-01-01")
    fmt = (lambda v: f"{v:,.0f}원") if market == "KR" else (lambda v: f"${v:,.2f}")
    lines = [f"📊 {today} {'국내' if market == 'KR' else '미국'} 마감 요약"]
    if last:
        lines.append(f"자산 {fmt(last)}" + (f" ({last / prev - 1:+.2%})" if prev else ""))
    buys = sum(o["side"] == "buy" for o in orders)
    lines.append(f"매수 {buys}건 · 매도 {len(orders) - buys}건 · 손절/익절 감시 {len(plans)}종목 · 올해 실현 손익 {fmt(year['total'])}")
    for o in orders:
        why = (o.get("message") or "").split(" | ")[0][:80]
        lines.append(f"· {'매수' if o['side'] == 'buy' else '매도'} {o['symbol']} {o['qty']}주 — {why}")
    for p in plans:
        lines.append(f"· 감시 {p['symbol']}: 손절 {p['stop']:,} / 익절 {p['take']:,}")
    return {"date": today, "market": market, "equity": last,
            "daily_return": (last / prev - 1) if last and prev else None, "orders": orders, "plans": plans,
            "realized_year": year, "text": "\n".join(lines)}


# 가격 알림 (관심 종목). 닿으면 알리고 목록에서 뺀다
def check_alerts(store, cfg: dict, price_of, save) -> list[dict]:
    alerts = cfg.get("alerts") or []
    hit, keep = [], []
    for a in alerts:
        try:
            px = float(price_of(a["symbol"]))
        except Exception:
            keep.append(a)
            continue
        if (a["op"] == "above" and px >= a["price"]) or (a["op"] == "below" and px <= a["price"]):
            notify(store, cfg, f"🔔 가격 알림: {a.get('name') or a['symbol']} {px:,} "
                               f"({a['price']:,} {'이상' if a['op'] == 'above' else '이하'} 도달)", "alert")
            hit.append(a | {"hit_price": px})
        else:
            keep.append(a)
    if hit:
        cfg["alerts"] = keep
        save(cfg)
    return hit


# AI 비용 (이번 달, 원)
def month_cost_krw(store) -> float:
    month = datetime.now().strftime("%Y-%m")
    total = float(store.get("recheck_cost_" + month, "0") or 0)  # 장중 AI 재점검
    for r in store.rows("select reports from ai_runs where substr(started_at,1,7)=?", (month,)):
        try:
            total += float(json.loads(r["reports"] or "{}").get("cost_usd") or 0)
        except (ValueError, TypeError, AttributeError):
            pass
    return total * KRW_PER_USD


# 절전 방지: 자동매매가 켜져 있으면 PC 가 잠들지 않게 (화면은 꺼져도 됨)
_awake = None


def keep_awake(on: bool) -> None:
    global _awake
    if os.name != "nt" or _awake == on:
        return
    import ctypes
    ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
    ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))
    _awake = on


# 윈도우 시작 시 자동 실행 (시작프로그램 폴더에 실행 파일을 두고 지운다)
def startup_file() -> Path | None:
    appdata = os.environ.get("APPDATA")
    if os.name != "nt" or not appdata:
        return None
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "QTrader.cmd"


def set_autostart(on: bool) -> bool:
    f = startup_file()
    if f is None:
        raise RuntimeError("윈도우에서만 쓸 수 있습니다")
    if on:
        exe = Path(sys.executable)
        if getattr(sys, "frozen", False):  # 설치 파일(QTrader.exe)로 실행 중
            cmd = f'"{exe}" supervise --no-browser'
        else:
            exe = exe.with_name("pythonw.exe") if exe.with_name("pythonw.exe").exists() else exe
            cmd = f'"{exe}" -m qtrader supervise --no-browser'
        home = os.environ.get("QTRADER_HOME", "")
        env = f'set "QTRADER_HOME={home}"\r\n' if home else ""
        f.write_text(f'@echo off\r\n{env}start "" {cmd}\r\n', encoding="utf-8")
    elif f.exists():
        f.unlink()
    return f.exists()


def autostart_on() -> bool:
    f = startup_file()
    return bool(f and f.exists())


# 수익 리포트: 체결된 주문을 산 순서대로(선입선출) 짝지어 '한 번 사고판 거래'로 만든다
EXIT_KO = {"stop": "손절", "take": "익절", "time": "기간 만료", "recheck": "AI 재점검", "manual": "직접 매도", "ai": "AI 판단"}


def performance(store, account: str, market: str, prices: dict | None = None) -> dict:
    from datetime import date as _date
    rows = store.rows("select * from orders where account=? and market=? and status='filled' order by id", (account, market))
    lots: dict[str, list[dict]] = {}
    trades = []
    for o in rows:
        if o["side"] == "buy":
            lots.setdefault(o["symbol"], []).append({"qty": o["qty"], "px": o["price"], "date": o["trade_date"]})
            continue
        left, msg = o["qty"], o.get("message") or ""
        why = ("stop" if msg.startswith("손절") else "take" if msg.startswith("익절") else "time" if msg.startswith("기간")
               else "recheck" if "재점검" in msg else "manual" if o.get("strategy") == "manual" else "ai")
        while left > 0 and lots.get(o["symbol"]):
            lot = lots[o["symbol"]][0]
            q = min(left, lot["qty"])
            pnl = q * (o["price"] - lot["px"])
            days = (_date.fromisoformat(o["trade_date"]) - _date.fromisoformat(lot["date"])).days
            trades.append({"symbol": o["symbol"], "qty": q, "buy_date": lot["date"], "sell_date": o["trade_date"],
                           "buy": lot["px"], "sell": o["price"], "pnl": pnl, "pct": o["price"] / lot["px"] - 1 if lot["px"] else 0,
                           "days": days, "why": EXIT_KO[why]})
            lot["qty"] -= q
            left -= q
            if lot["qty"] <= 0:
                lots[o["symbol"]].pop(0)
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    realized = sum(t["pnl"] for t in trades)
    # 누적 손익 곡선과 최대 낙폭
    curve, cum, peak, mdd = [], 0.0, 0.0, 0.0
    for t in sorted(trades, key=lambda x: x["sell_date"]):
        cum += t["pnl"]
        peak = max(peak, cum)
        mdd = min(mdd, cum - peak)
        if curve and curve[-1]["time"] == t["sell_date"]:
            curve[-1]["value"] = cum
        else:
            curve.append({"time": t["sell_date"], "value": cum})
    open_pnl = 0.0
    for s, ls in lots.items():
        px = (prices or {}).get(s)
        if px:
            open_pnl += sum(l["qty"] * (px - l["px"]) for l in ls)
    gw, gl = sum(t["pnl"] for t in wins), -sum(t["pnl"] for t in losses)
    return {"trades": list(reversed(trades)), "curve": curve, "summary": {
        "realized": realized, "open": open_pnl, "total": realized + open_pnl, "count": len(trades),
        "win_rate": len(wins) / len(trades) if trades else None,
        "avg_win": gw / len(wins) if wins else 0, "avg_loss": -gl / len(losses) if losses else 0,
        "profit_factor": gw / gl if gl else (None if not wins else float("inf")),
        "mdd": mdd, "avg_days": sum(t["days"] for t in trades) / len(trades) if trades else None,
        "best": max(trades, key=lambda t: t["pnl"]) if trades else None, "worst": min(trades, key=lambda t: t["pnl"]) if trades else None}}
