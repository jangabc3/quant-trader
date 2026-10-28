"""실시간 사무실: 장중 현황판과 AI 긴급 재점검.

세 겹으로 움직인다.
1) 실시간 움직임 (비용 0): 감시 중 종목의 손절·익절까지 거리, 오늘 한 일, 보유 종목 뉴스 -> 캐릭터 말풍선
2) 다음 후보 재계산 (비용 0): N분(기본 15)마다 숫자 규칙을 다시 돌려 '무엇이 모자라는지'까지 보여 준다
3) AI 긴급 재점검 (하루 상한): 가진 종목에 일이 생겼을 때만 빠른 모델로 한 번 묻는다.
   위험을 줄이는 쪽(일찍 팔기)만 바로 한다. 새로 사는 것은 정식 AI 회의와 체크리스트를 거친다
   (장중 계속이면 scheduler._intraday, 아니면 정기 실행).
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta

from . import principles, screener
from .engine import TZ, trade_date
from .models import Market, OrderRequest, Side

_cache: dict = {}

RECHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["hold", "sell"]},
        "reason": {"type": "string"},
        "watch": {"type": "string"},
    },
    "required": ["action", "reason", "watch"],
    "additionalProperties": False,
}


def _cached(key: str, ttl: float, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    _cache[key] = (time.time(), val)
    return val


def watch_list(ctx, market: Market) -> list[dict]:
    """감시 중 종목: 지금 가격이 손절~익절 사이 어디쯤인지."""
    out = []
    try:
        acct = ctx.broker.account(market)
    except Exception:
        return out
    plans = {p["symbol"]: p for p in ctx.store.active_plans(ctx.broker.name, market.value)}
    for s, pos in acct.positions.items():
        p = plans.get(s)
        px = pos.last_price or pos.avg_price
        row = {"symbol": s, "qty": pos.qty, "avg": pos.avg_price, "price": px,
               "pnl": (px / pos.avg_price - 1) if pos.avg_price else 0}
        if p:
            row |= {"stop": p["stop"], "take": p["take"], "to_stop": px / p["stop"] - 1 if p["stop"] else None,
                    "to_take": p["take"] / px - 1 if px else None,
                    "pos": max(0.0, min(1.0, (px - p["stop"]) / (p["take"] - p["stop"]))) if p["take"] > p["stop"] else None}
        out.append(row)
    return out


def next_candidates(ctx, market: Market, n: int = 8, ttl: float = 900) -> list[dict]:
    """15분(ttl)마다 다시 계산. 통과한 종목 + 아깝게 떨어진 종목(무엇이 모자라는지)."""
    def build():
        R = principles.DEFAULT_RULES | (ctx.cfg["ai"].get("rules") or {})
        syms, held = screener.pool(ctx, market)
        rows = []
        for s in syms[:60]:  # 15분마다 도니까 시세 호출 수를 제한
            if s in held:
                continue
            try:
                sig = principles.market_signals(ctx.data.bars(market, s, 300))
            except Exception:
                continue
            import math
            if any(not isinstance(sig.get(k), (int, float)) or math.isnan(sig[k]) for k in ("last", "sma200", "mom12_1", "rsi", "vol")):
                continue  # 상장한 지 얼마 안 돼 자료가 모자란 종목 (RSI·200일선을 계산할 수 없음)
            miss = []
            if R["require_trend"] and sig["last"] <= sig["sma200"]:
                miss.append(f"200일선 아래 ({sig['last'] / sig['sma200'] - 1:+.0%})")
            if R["require_momentum"] and sig["mom12_1"] <= 0:
                miss.append(f"모멘텀 음수 ({sig['mom12_1']:+.0%})")
            if sig["rsi"] >= R["max_rsi"]:
                miss.append(f"과열 RSI {sig['rsi']:.0f} (식으면 후보)")
            if sig["vol"] > R["max_vol"]:
                miss.append(f"변동성 {sig['vol']:.0%} 너무 큼")
            rows.append({"symbol": s, "score": sig["mom12_1"] / max(sig["vol"], 0.05), "missing": miss, "origin": screener.ORIGIN.get(s, ""),
                         "rsi": round(sig["rsi"]), "mom": sig["mom12_1"]})
        rows.sort(key=lambda r: (len(r["missing"]), -r["score"]))
        return rows[:n]
    return _cached(f"cand:{market.value}:{ctx.broker.name}", ttl, build)


def schedule(ctx, market: Market) -> dict:
    from .scheduler import SESSIONS, next_run, trading_now
    mc = ctx.cfg["markets"][market.value]
    nr = next_run(market, mc["run_time"])
    before = int(ctx.cfg["ai"]["run_before_minutes"])
    t = datetime.now(TZ[market])
    close = t.replace(hour=SESSIONS[market][1].hour, minute=SESSIONS[market][1].minute, second=0, microsecond=0)
    from .scheduler import session_name
    return {"open": trading_now(ctx.cfg, market), "session": session_name(ctx.cfg, market), "now": t.isoformat(timespec="seconds"), "next_run": nr.isoformat(timespec="minutes"),
            "next_meeting": (nr - timedelta(minutes=before)).isoformat(timespec="minutes"),
            "close": close.isoformat(timespec="minutes"), "strategy": mc["strategy"], "enabled": mc["enabled"]}


def timeline(ctx, market: Market, limit: int = 30, name_of=lambda s: s) -> list[dict]:
    today = datetime.now(TZ[market]).strftime("%Y-%m-%d")
    rows = ctx.store.rows("select * from events where substr(ts,1,10)>=? order by id desc limit 200", (today,))
    keep = ("engine", "exit", "sync", "ai", "picks", "alert", "order", "risk", "live", "manual", "report", "approval", "intraday")
    out = []
    for r in rows:
        src = r["source"].split(".")[0]
        if src in keep and (market.value in r["source"] or "." not in r["source"] or src in ("exit", "sync", "order", "manual", "live")):
            from .humanize import say
            out.append({"id": r["id"], "ts": r["ts"], "level": r["level"], "source": src, "message": say(r["message"], name_of)[:200]})
        if len(out) >= limit:
            break
    return out


def headlines(ctx, names: list[str], limit: int = 6) -> list[dict]:
    """보유·후보 종목 이름이 들어간 최신 뉴스 (RSS, 비용 0)."""
    if not names:
        return []
    from . import news as news_mod

    def build():
        try:
            return news_mod.feeds(ctx.cfg.get("news", {}).get("feeds"))["items"]
        except Exception:
            return []
    items = _cached("news", 300, build)
    out = []
    for it in items:
        hit = next((n for n in names if n and len(n) >= 2 and n in it["title"]), None)
        if hit:
            out.append({"name": hit, "title": it["title"], "link": it["link"], "time": it.get("time"), "source": it.get("source")})
        if len(out) >= limit:
            break
    return out


# AI 긴급 재점검
def _month_key() -> str:
    return "recheck_cost_" + datetime.now().strftime("%Y-%m")


def recheck_cost_krw(store) -> float:
    from .extras import KRW_PER_USD
    return float(store.get(_month_key(), "0") or 0) * KRW_PER_USD


def triggers(ctx, market: Market, watch: list[dict]) -> list[tuple[str, str]]:
    """재점검이 필요한 보유 종목과 이유."""
    out = []
    for w in watch:
        try:
            bars = ctx.data.bars(market, w["symbol"], 3)
            prev = float(bars["close"].iloc[-2]) if len(bars) > 1 else w["avg"]
        except Exception:
            prev = w["avg"]
        day = w["price"] / prev - 1 if prev else 0
        if day <= -0.03:
            out.append((w["symbol"], f"장중 {day:+.1%} 급락"))
        elif w.get("to_stop") is not None and w["to_stop"] < 0.02:
            out.append((w["symbol"], f"손절선까지 {w['to_stop']:.1%} 남음"))
    return out


def run_rechecks(ctx, market: Market, llm=None) -> list[dict]:
    """장중 스케줄러가 부른다. 하루 상한·월 비용 상한 안에서, 같은 종목은 하루 한 번만."""
    from .agents.llm import LLM, LLMError
    from .extras import month_cost_krw, notify

    ai = ctx.cfg["ai"]
    cap_day = int(ai.get("recheck_per_day", 5))
    if not ai.get("live_recheck", True) or cap_day <= 0:
        return []
    today = trade_date(market)
    done_key = f"recheck_{market.value}_{today}"
    done = json.loads(ctx.store.get(done_key) or "{}")
    if len(done) >= cap_day:
        return []
    cap = float(ai.get("monthly_cost_cap_krw") or 0)
    if cap and month_cost_krw(ctx.store) >= cap:
        return []
    watch = watch_list(ctx, market)
    out = []
    for sym, why in triggers(ctx, market, watch):
        if sym in done or len(done) >= cap_day:
            continue
        w = next(x for x in watch if x["symbol"] == sym)
        news = headlines(ctx, [sym], 5)
        user = (f"보유 종목 {sym} 장중 재점검. 계기: {why}.\n현재가 {w['price']:,} / 평균단가 {w['avg']:,} / 손익 {w['pnl']:+.1%}"
                + (f" / 손절가 {w['stop']:,} / 익절가 {w['take']:,}" if w.get("stop") else "")
                + "\n최근 관련 뉴스:\n" + ("\n".join(f"- {n['title']}" for n in news) or "- 없음")
                + "\n\n규칙: 손절가가 이미 있으므로 대부분은 hold 이다. 회사의 근본이 바뀐 악재(분식, 거래정지 위험, 실적 쇼크 확정 등)가 확인될 때만 sell."
                  " reason 은 초보자도 알 한국어 두 문장, watch 는 다음에 볼 것 한 문장.")
        try:
            r = (llm or LLM(ai["quick_model"], "low")).run("너는 신중한 리스크 관리자다. 추측으로 팔지 않는다.", user,
                                                            schema=RECHECK_SCHEMA, max_tokens=800)
        except (LLMError, Exception) as e:
            ctx.store.log("warn", f"live.{market.value}", f"{sym} AI 재점검 실패: {e}")
            done[sym] = "error"
            continue
        ctx.store.set(_month_key(), str(float(ctx.store.get(_month_key(), "0") or 0) + r.cost_usd()))
        d = r.data or {"action": "hold", "reason": r.text[:200], "watch": ""}
        done[sym] = d["action"]
        msg = f"{sym} AI 재점검({why}): {'일찍 판다' if d['action'] == 'sell' else '계속 보유'} — {d['reason']}"
        ctx.store.log("notice" if d["action"] == "sell" else "info", f"live.{market.value}", msg)
        if d["action"] == "sell" and w["qty"] > 0:
            from .guard import release
            release(ctx, market, sym)
            from .engine import kr_tick
            from .scheduler import is_open
            # 미국은 지정가만, 국내는 정규장이면 시장가·넥스트레이드(시간외)면 지정가만 되므로 현재가보다 1% 낮게 호가 단위로
            px = round(w["price"] * 0.997, 2) if market == Market.US else 0 if is_open(market) else kr_tick(w["price"] * 0.99)
            res = ctx.broker.order(OrderRequest(market, sym, Side.SELL, int(w["qty"]), px, "ai-exit", f"AI 재점검 매도: {d['reason']}"))
            status = ("filled" if res.filled_qty else "submitted") if res.ok else "rejected"
            ctx.store.add_order(trade_date=today, account=ctx.broker.name, market=market.value, symbol=sym, side="sell",
                                qty=int(w["qty"]), price=res.filled_price or w["price"], status=status, broker_order_id=res.broker_order_id,
                                strategy="ai-exit", message=f"AI 재점검 매도: {d['reason']} | {res.message}")
            if res.ok and (status == "filled" or ctx.broker.name != "toss"):
                ctx.store.close_plan(ctx.broker.name, market.value, sym, "recheck")
            notify(ctx.store, ctx.cfg, "🧯 " + msg, "live")
        out.append({"symbol": sym, "why": why} | d)
    ctx.store.set(done_key, json.dumps(done, ensure_ascii=False))
    return out


def today_plan(ctx, market: Market, name_of=lambda s: s) -> dict:
    """AI 사무실 '오늘 AI 회의' 판: 자동으로 무엇을 하고 있는지 한눈에 (AI 호출 없음).
    상태 한 줄(headline) + 오늘 회의 수 + 다음 회의 시각 + 후보마다 지금 단계."""
    from datetime import timezone
    from .agents import meeting
    from .config import meetings_cap, met_today
    from .engine import buy_blocked
    from .extras import month_cost_krw
    from .scheduler import session_name, trading_now
    cfg, ai = ctx.cfg, ctx.cfg["ai"]
    today = trade_date(market)
    mc = cfg["markets"][market.value]
    cap, met = meetings_cap(cfg, market), met_today(ctx.store, market, today)
    auto = ctx.store.get("auto_trading", "0") == "1"
    open_ = trading_now(cfg, market)
    every = int(ai.get("intraday_every_min", 15))
    state = json.loads(ctx.store.get(f"intraday_{market.value}_{today}") or "{}")
    runs = {r["symbol"]: r for r in reversed(ctx.store.ai_runs(300)) if r["trade_date"] == today and r["market"] == market.value}
    live = {r["symbol"]: r for r in meeting.live_runs() if r["status"] == "running"}
    picks = json.loads(ctx.store.get(f"picks_{market.value}_{today}") or "[]")
    last = ctx.store.get(f"intraday_last_{market.value}")
    next_at = None
    if last:
        nx = datetime.fromisoformat(last) + timedelta(minutes=every)
        next_at = max(nx, datetime.now(timezone.utc)).isoformat(timespec="minutes")
    cap_krw = float(ai.get("monthly_cost_cap_krw") or 0)
    try:
        blocked = buy_blocked(ctx, market)
    except Exception:
        blocked = None
    # 지금 상태 한 줄 (가장 먼저 걸리는 것 하나)
    left = max(cap - len(met), 0)
    if not mc.get("enabled"):
        head, tone = "이 시장은 자동매매를 꺼 두었어요 (설정 → 3. 투자 방식).", "off"
    elif cap == 0 or mc.get("strategy") != "ai":
        head, tone = "이 시장은 AI 회의를 꺼 두었어요 (설정 → 3. 투자 방식). 손절·익절 매도는 계속해요.", "off"
    elif not auto:
        head, tone = "자동매매가 꺼져 있어요. 켜면 장이 열린 동안 아래 후보를 하나씩 AI 회의하고, 결론이 매수면 바로 사요.", "off"
    elif ctx.kill:
        head, tone = "긴급 정지 중이라 새로 사지 않아요. 손절·익절 매도는 계속해요.", "warn"
    elif not open_:
        head, tone = "지금은 장이 닫혀 있어요. 장이 열리면 아래 후보부터 차례로 회의해요.", "wait"
    elif live:
        r = next(iter(live.values()))
        head, tone = f"지금 {name_of(r['symbol'])} AI 회의 중이에요 ({r.get('stage', '')}). 결론이 매수이고 기준을 통과하면 바로 사요.", "busy"
    elif left == 0:
        head, tone = f"오늘 회의 {cap}번을 다 썼어요. 내일 다시 회의해요. 가진 종목의 손절·익절 감시는 계속해요.", "done"
    elif cap_krw and month_cost_krw(ctx.store) >= cap_krw:
        head, tone = "이번 달 AI 비용 상한에 닿아 회의를 쉬어요 (설정 → 5. 알림·비용).", "warn"
    elif blocked:
        head, tone = f"오늘은 더 사지 않아 회의를 쉬어요: {blocked}. 손절·익절 감시는 계속해요.", "done"
    else:
        waiting = [p for p in picks if p["symbol"] not in met and p["symbol"] not in state and not p.get("held")]
        if waiting:
            head, tone = f"장이 열려 있어요. 다음 회의는 {name_of(waiting[0]['symbol'])} 예정이에요 ({every}분마다 한 종목씩).", "busy"
        else:
            head, tone = (f"오늘 기준을 통과한 후보를 모두 회의했어요. {every}분마다 새 후보를 찾고, 생기면 바로 회의해요.", "wait")
    items = []
    for p in picks:
        s = p["symbol"]
        e, r = state.get(s) or {}, runs.get(s)
        rating = ((r or {}).get("final") or {}).get("rating") or (r or {}).get("rating")
        if s in live:
            st, note = "meeting", live[s].get("stage", "")
        elif e.get("state") == "bought":
            st, note = "bought", f"{e.get('qty', '')}주 샀어요"
        elif e.get("state") == "approval":
            st, note = "approval", "승인을 기다려요"
        elif e.get("state") == "error" or (r and r.get("status") == "error"):
            st, note = "error", (e.get("note") or (r or {}).get("error") or "")[:80]
        elif rating == "Hold":
            st, note = "hold", "보류: 지금은 사지 않아요"
        elif rating in ("Buy", "Overweight"):
            st, note = "nobuy", e.get("note") or "매수 의견이지만 기준에 걸려 사지 않았어요"
        elif rating in ("Sell", "Underweight"):
            st, note = "sell", "매도 의견이라 사지 않아요"
        elif p.get("held"):
            st, note = "held", "가진 종목 (손절·익절 감시 중)"
        else:
            st, note = "wait", "회의 대기"
        items.append({"symbol": s, "name": name_of(s), "score": p.get("score"), "reason": p.get("reason", ""), "factors": p.get("factors"),
                      "state": st, "note": note, "run_id": (r or {}).get("id") or e.get("run_id"), "held": p.get("held", False)})
    return {"market": market.value, "auto": auto, "open": open_, "session": session_name(cfg, market), "cap": cap,
            "used": len(met), "every": every, "next_at": next_at, "headline": head, "tone": tone, "items": items}


def intraday_board(ctx, market: Market, name_of=lambda s: s) -> dict:
    """장중 회의 -> 매수 흐름: 오늘 장중에 연 회의와 그 결과 (scheduler._intraday 가 남긴 기록)."""
    from .agents import meeting
    ai = ctx.cfg["ai"]
    today = trade_date(market)
    state = json.loads(ctx.store.get(f"intraday_{market.value}_{today}") or "{}")
    items = []
    for sym, e in state.items():
        row = {"symbol": sym, "name": name_of(sym)} | e
        if e.get("state") == "meeting":
            run = meeting.get_run(e.get("run_id", ""))
            row["stage"] = (run or {}).get("stage", "")
        items.append(row)
    from .config import meetings_cap, met_today
    cap = meetings_cap(ctx.cfg, market)
    return {"on": bool(ai.get("intraday", True)) and cap > 0, "every": int(ai.get("intraday_every_min", 15)),
            "met": len(met_today(ctx.store, market, today)), "cap": cap, "items": items[::-1]}


def snapshot(ctx, market: Market, name_of=lambda s: s) -> dict:
    """현황판 한 번에: 감시·후보·오늘 한 일·일정·뉴스·재점검."""
    watch = [w | {"name": name_of(w["symbol"])} for w in watch_list(ctx, market)]
    cands = [c | {"name": name_of(c["symbol"])} for c in next_candidates(ctx, market)]
    names = [w["name"] for w in watch] + [c["name"] for c in cands[:3]]
    today = trade_date(market)
    return {"market": market.value, "auto": ctx.store.get("auto_trading", "0") == "1", "kill": ctx.kill,
            "schedule": schedule(ctx, market), "watch": watch, "candidates": cands,
            "timeline": timeline(ctx, market, name_of=name_of), "news": headlines(ctx, names),
            "rechecks": json.loads(ctx.store.get(f"recheck_{market.value}_{today}") or "{}"),
            "recheck_cap": int(ctx.cfg["ai"].get("recheck_per_day", 5)), "intraday": intraday_board(ctx, market, name_of)}
