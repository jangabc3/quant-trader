"""로컬 대시보드 서버. 127.0.0.1 에서만 열리고, 변경 요청은 실행마다 새로 만드는 토큰이 있어야 한다."""
from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import __version__, backtest, extras, config as config_mod, fundamentals, market as market_mod, news as news_mod, principles, profile as profile_mod, stockdata, strategies, vault
from ..agents import meeting
from ..agents.assistant import Assistant
from ..agents.llm import LLMError
from ..agents.roles import RATING_KO, ROLES, STAGES
from ..data import panel
from ..engine import build_context, execute_approval, run_market, toss_client
from ..agents import track
from ..agents.context import key_levels, market_of
from .. import indicators as ind
from ..models import Market
from ..scheduler import Scheduler, is_open, next_run
from ..storage import Store

STATIC = Path(__file__).parent / "static"
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}
SECRET_KINDS = ("kis_demo", "kis_real", "toss", "anthropic", "dart", "telegram")


class BacktestIn(BaseModel):
    market: str = "KR"
    strategy: str = "momentum"
    params: dict = {}
    universe: list[str] | None = None
    days: int = 750


class RunIn(BaseModel):
    market: str = "KR"
    dry_run: bool = True


class ToggleIn(BaseModel):
    on: bool


class PaperResetIn(BaseModel):
    market: str = "ALL"  # KR, US, ALL


class SecretIn(BaseModel):
    kind: str
    key: str
    secret: str = ""


class MeetingIn(BaseModel):
    symbol: str


class ProfileIn(BaseModel):
    answers: dict


class DecideIn(BaseModel):
    approve: bool


class OrderIn(BaseModel):
    symbol: str
    side: str
    qty: int
    price: float = 0  # 0 = 시장가
    stop: float = 0  # 직접 매수 때 정하는 손절가 (0 = 안 정함)
    take: float = 0


class PlanIn(BaseModel):
    symbol: str
    stop: float
    take: float


class ModifyIn(BaseModel):
    price: float = 0
    qty: int = 0
    symbol: str = ""


class CondIn(BaseModel):
    symbol: str
    side: str
    qty: int
    trigger: float
    price: float = 0
    days: int = 30
    note: str = ""
    then_stop: float = 0


class CondEdit(BaseModel):
    trigger: float
    price: float = 0
    qty: int = 0


class SplitIn(BaseModel):
    symbol: str
    side: str
    qty: int  # 전체 수량
    parts: int = 2  # 2~4번에 나눠서
    step_pct: float = 3.0  # 다음 번은 이만큼 더 싸게(매수)/비싸게(매도)
    price: float = 0  # 첫 번째 주문 가격 (0 = 현재가 지정가)


class TestIn(BaseModel):
    kind: str


class AlertIn(BaseModel):
    symbol: str
    name: str = ""
    op: str = "above"
    price: float


class ChatIn(BaseModel):
    text: str
    thread_id: str | None = None
    page: str = ""


def session_token() -> str:
    """변경 요청용 토큰. 데이터 폴더에 한 번 만들어 두고 재사용한다.
    서버를 다시 켜도 이미 열린 페이지의 저장 요청이 거부되지 않게 하기 위해서다."""
    p = config_mod.home() / "session_token"
    try:
        t = p.read_text(encoding="utf-8").strip()
        if len(t) >= 32:
            return t
    except OSError:
        pass
    t = secrets.token_urlsafe(32)
    p.write_text(t, encoding="utf-8")
    return t


def key_state() -> dict:
    return {"kis_demo": vault.has_kis("demo"), "kis_real": vault.has_kis("real"),
            "toss": vault.get_toss() is not None, "anthropic": bool(vault.get_anthropic()),
            "dart": bool(vault.get("dart_api_key")), "telegram": bool(vault.get("telegram_bot_token"))}


def create_app(scheduler: Scheduler | None = None, llm=None, assistant_client=None) -> FastAPI:
    app = FastAPI(title="QTrader", version=__version__, docs_url=None, redoc_url=None)
    token = session_token()
    # 정적 파일 주소에 붙여 브라우저가 예전 파일을 쓰지 않게 한다 (서버를 켤 때마다 바뀜)
    import time as _time
    build = str(int(_time.time()))
    sched = scheduler or Scheduler()
    app.state.token = token

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        # DNS 리바인딩 방지: Host 헤더가 로컬일 때만 응답
        host = (request.headers.get("host") or "").split(":")[0]
        if host not in ALLOWED_HOSTS and host != "testserver":
            return HTMLResponse("forbidden", status_code=403)
        return await call_next(request)

    def need_token(x_qtrader_token: str = Header("")):
        if not secrets.compare_digest(x_qtrader_token, token):
            raise HTTPException(403, "토큰이 올바르지 않습니다. 페이지를 새로고침하세요")

    _names: dict = {"t": 0.0, "map": {}}

    def name_of(sym: str) -> str:
        """종목 코드 -> 이름 (토스 종목 목록, 없으면 체험용 목록). 1시간마다 새로 읽는다."""
        import time as _t
        if _t.time() - _names["t"] > 3600 or not _names["map"]:
            m = {s: n for s, n, _ in market_mod.DEMO_KR + market_mod.DEMO_US}
            try:
                m |= {r["symbol"]: r["name"] for r in stockdata.master(toss(), config_mod.home())}
            except Exception:
                pass
            _names.update(t=_t.time(), map=m)
        return _names["map"].get(sym.split(":")[-1], sym)

    def ctx():
        try:
            return build_context()
        except RuntimeError as e:
            raise HTTPException(400, str(e))

    def toss():
        return toss_client(config_mod.load())

    assistant = Assistant(ctx, toss, config_mod.load, client=assistant_client)

    def warm_market() -> None:
        """시장 화면의 무거운 칸(외국인·기관 매매 동향은 종목 40개를 하나씩 묻는다)을 켜자마자 뒤에서 미리 받아 둔다.
        처음 누를 때 몇 초씩 기다리지 않게. 시험(오프라인)·키 없음이면 하지 않는다."""
        import os
        import threading as _th
        if os.environ.get("QTRADER_OFFLINE"):
            return

        def run():
            t = toss()
            if t is None:
                return
            for f in (lambda: market_mod.overview(t), lambda: market_mod.rankings(t, "amount", "KR"),
                      lambda: market_mod.industries(t, "KR"), lambda: market_mod.investor_flows(t, "buy"),
                      lambda: market_mod.industries(t, "US")):
                try:
                    f()
                except Exception:
                    pass
        _th.Thread(target=run, name="warm-market", daemon=True).start()
    warm_market()

    @app.get("/", response_class=HTMLResponse)
    def index():
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        # 파일을 고치면 서버를 다시 켜지 않아도 새 파일을 받게 가장 최근 수정 시각을 붙인다
        stamp = max(int(f.stat().st_mtime) for f in STATIC.glob("*.*"))
        return html.replace("__TOKEN__", token).replace("__VERSION__", __version__).replace("__BUILD__", f"{build}.{stamp}")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/api/status")
    def status():
        cfg = config_mod.load()
        c = None
        err = ""
        try:
            c = build_context(cfg)
        except RuntimeError as e:
            err = str(e)
        markets = {}
        for m in (Market.KR, Market.US):
            mc = cfg["markets"][m.value]
            info = {"enabled": mc["enabled"], "strategy": mc["strategy"], "run_time": mc["run_time"],
                    "open": is_open(m), "next_run": next_run(m, mc["run_time"]).isoformat(timespec="minutes"),
                    "budget": mc["budget"]}
            if c and mc["enabled"]:
                try:
                    a = c.broker.account(m)
                    info.update(cash=a.cash, equity=a.equity, positions=len(a.positions))
                except Exception as e:
                    info["account_error"] = str(e)
            markets[m.value] = info
        st = c.store if c else None
        return {
            "version": __version__, "mode": cfg["mode"], "live_confirmed": cfg.get("live_confirmed", False),
            "broker": c.broker.name if c else None, "data_source": c.data.name if c else None,
            "kill_switch": bool(st and st.get("kill_switch", "0") == "1"),
            "kill_since": st.get("kill_since") if st else None, "kill_reason": st.get("kill_reason") if st else None,
            "auto_trading": bool(st and st.get("auto_trading", "0") == "1"),
            "keys": key_state(), "markets": markets, "error": err, "scheduler_error": sched.last_error,
        }

    @app.get("/api/positions")
    def positions():
        c = ctx()
        out = []
        from ..guard import guarded
        from ..corp import event_of as corp_event
        g = guarded(c)
        for m in (Market.KR, Market.US):
            if not c.cfg["markets"][m.value]["enabled"]:
                continue
            a = c.broker.account(m)
            for p in a.positions.values():
                pnl = (p.last_price / p.avg_price - 1) if p.avg_price else 0
                out.append({"market": m.value, "symbol": p.symbol, "name": name_of(p.symbol), "qty": p.qty, "avg_price": p.avg_price,
                            "last_price": p.last_price, "value": p.value, "pnl": round(pnl, 4), "day_pnl": p.day_pnl,
                            "server_guard": f"{m.value}:{p.symbol}" in g, "event": corp_event(c.store, m.value, p.symbol)})
        return out

    @app.get("/api/orders")
    def orders(limit: int = 100):
        return [o | {"name": name_of(o["symbol"])} for o in ctx().store.orders(limit)]

    @app.get("/api/events")
    def events(limit: int = 100):
        from ..humanize import say
        return [e | {"message": say(e["message"], name_of)} for e in ctx().store.events(limit)]

    @app.get("/api/equity")
    def equity(market: str = "KR"):
        c = ctx()
        return c.store.equity_history(c.broker.name, market)

    @app.get("/api/strategies")
    def strategy_list():
        return {k: {f: v.default for f, v in cls.__dataclass_fields__.items()}
                for k, cls in strategies.REGISTRY.items()}

    # 시장 한눈에 보기
    @app.get("/api/market/overview")
    def market_overview():
        try:
            return market_mod.overview(toss())
        except Exception as e:
            raise HTTPException(502, f"시장 정보를 받지 못했습니다: {e}")

    @app.get("/api/fx")
    def fx_rate():
        """달러 환율 (원). 토스 키가 없으면 체험용 값."""
        t = toss()
        if t is None:
            return {"rate": 1390.0, "demo": True}
        from ..toss import num
        try:
            f = market_mod.cached("fx_rate", 60, t.exchange_rate) or {}
            return {"rate": num(f.get("rate")) or None, "demo": False}
        except Exception as e:
            raise HTTPException(502, f"환율을 받지 못했습니다: {e}")

    @app.get("/api/market/rankings")
    def market_rankings(kind: str = "amount", market: str = "KR", duration: str = "realtime", count: int = 30, safe: bool = False):
        if market not in ("KR", "US"):
            raise HTTPException(400, "market 은 KR 또는 US")
        try:
            return market_mod.rankings(toss(), kind, market, duration, min(max(count, 1), 100), safe)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            raise HTTPException(502, f"순위를 받지 못했습니다: {e}")

    @app.get("/api/market/watch")
    def market_watch():
        syms = config_mod.load().get("watchlist", [])
        t = toss()
        if t is None:  # 토스 키가 없으면 체험용 시세로라도 보여 준다
            out = []
            for s in syms:
                try:
                    d = stockdata.summary(None, s)
                    out.append({"symbol": s, "name": d["name"], "price": d["price"], "change": d["change"],
                                "change_rate": d["change_rate"], "demo": True})
                except Exception:
                    continue
            return out
        try:
            return market_mod.watch_quotes(t, syms)
        except Exception as e:
            raise HTTPException(502, str(e))

    # 종목 화면
    @app.get("/api/stocks/search")
    def stock_search(q: str = "", limit: int = 20):
        rows = stockdata.master(toss(), config_mod.home())
        return stockdata.search(rows, q, min(max(limit, 1), 50))

    def guard(fn):
        try:
            return fn()
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            raise HTTPException(502, f"데이터를 받지 못했습니다: {e}")

    @app.get("/api/stock/{symbol}/summary")
    def stock_summary(symbol: str):
        return guard(lambda: stockdata.summary(toss(), symbol))

    @app.get("/api/stock/{symbol}/candles")
    def stock_candles(symbol: str, interval: str = "1d", count: int | None = None):
        return guard(lambda: stockdata.candles(toss(), symbol.upper(), interval, count))

    @app.get("/api/stock/{symbol}/orderbook")
    def stock_orderbook(symbol: str):
        return guard(lambda: stockdata.orderbook(toss(), symbol.upper()))

    @app.get("/api/stock/{symbol}/trades")
    def stock_trades(symbol: str):
        return guard(lambda: stockdata.trades(toss(), symbol.upper()))

    @app.get("/api/stock/{symbol}/flows")
    def stock_flows(symbol: str):
        t = toss()
        if t is None or not symbol[:1].isdigit():
            return {"records": []}
        return guard(lambda: t.stock_investor_trading(symbol.upper(), 20))

    @app.get("/api/stock/{symbol}/financials")
    def stock_financials(symbol: str):
        return fundamentals.for_symbol(toss(), symbol)

    @app.get("/api/stock/{symbol}/news")
    def stock_news(symbol: str, name: str = ""):
        q = name or symbol
        try:
            return {"items": news_mod.stock_news(f"{q} 주가")}
        except Exception as e:
            return {"items": [], "error": str(e)}

    # 섹터
    DEMO_SECTORS = [("100", "반도체", 0.0179), ("200", "2차전지", -0.0112), ("300", "바이오", 0.0064), ("400", "자동차", 0.0091),
                    ("500", "인터넷·플랫폼", -0.0043), ("600", "조선", 0.0215), ("700", "방산", 0.0132), ("800", "은행", 0.0021),
                    ("900", "게임", -0.0187), ("1000", "화장품", 0.0048), ("1100", "건설", -0.0066), ("1200", "AI 소프트웨어", 0.0251)]

    @app.get("/api/market/sectors")
    def market_sectors(kind: str = "TOP_GAINERS", market: str = "KR", duration: str = "1d"):
        t = toss()
        if t is None:
            return {"demo": True, "items": [{"rank": i + 1, "sectorId": sid, "name": n, "changeRate": r,
                                             "tradingAmount": None, "marketCap": None}
                                            for i, (sid, n, r) in enumerate(sorted(DEMO_SECTORS, key=lambda x: -x[2]))]}
        rows = guard(lambda: t.sector_rankings(kind, market, duration))
        from ..toss import num
        return {"demo": False, "items": [{"rank": x.get("rank"), "sectorId": x.get("sectorId"), "name": x.get("name"),
                                          "changeRate": num(x.get("changeRate")), "tradingAmount": num(x.get("tradingAmount")) or None,
                                          "marketCap": num(x.get("marketCapitalization")) or None} for x in rows]}

    @app.get("/api/market/industries")
    def market_industries(market: str = "KR", kind: str = "TOP_GAINERS", duration: str = "1d"):
        return guard(lambda: market_mod.industries(toss(), market, kind, duration))

    @app.get("/api/market/industry/{sector_id}")
    def market_industry(sector_id: str, market: str = "KR"):
        return guard(lambda: market_mod.industry_detail(toss(), sector_id, market))

    @app.get("/api/market/industry/{sector_id}/chart")
    def market_industry_chart(sector_id: str, market: str = "KR"):
        return guard(lambda: market_mod.industry_chart(toss(), sector_id, market))

    # 자동 업데이트
    @app.get("/api/update")
    def update_check(force: bool = False):
        from .. import updater
        c = ctx()
        if not (c.cfg.get("update") or {}).get("check", True) and not force:
            return {"current": __version__, "available": False}
        src = updater.source_info(config_mod.home())  # A: 내 PC 소스 폴더로 설치했으면 git 으로 확인
        out = updater.check_git(src, c.store, force) if src else updater.check(c.cfg, c.store, force)
        recent = updater.just_updated(src, c.store) if src else None
        return out | ({"recent": recent} if recent else {})

    @app.post("/api/update/seen", dependencies=[Depends(need_token)])
    def update_seen():
        """'이번 업데이트로 바뀐 것'을 봤다고 표시 (다시 안 보이게)."""
        from .. import updater
        src = updater.source_info(config_mod.home())
        if src and src.get("rev"):
            ctx().store.set("update_seen", src["rev"])
        return {"ok": True}

    @app.post("/api/update/apply", dependencies=[Depends(need_token)])
    def update_apply():
        from .. import updater
        c = ctx()
        if not updater.frozen():
            raise HTTPException(400, "개발 버전은 git pull 로 받아 주세요 (자동 업데이트는 설치본 QTrader.exe 에서만)")
        import os
        import threading
        src = updater.source_info(config_mod.home())
        if src:  # A: 꺼진 뒤 'QTrader 설치.cmd --update' 가 받기 -> 만들기 -> 설치 -> 다시 켜기
            info = updater.check_git(src, c.store, force=True)
            if not info.get("available"):
                raise HTTPException(400, info.get("reason") or "이미 최신 버전이에요")
            try:
                updater.write_git_pending(src, config_mod.home())
            except ValueError as e:
                raise HTTPException(400, str(e))
            c.store.log("notice", "update", f"업데이트 {info['current_rev']} -> {info['latest']} ({info['behind']}개 바뀜): 다시 만들어 설치합니다")
            threading.Timer(1.5, lambda: os._exit(updater.RESTART_CODE)).start()
            return {"ok": True, "latest": info["latest"], "mode": "git"}
        info = updater.check(c.cfg, c.store, force=True)
        if not info.get("available"):
            raise HTTPException(400, info.get("reason") or "이미 최신 버전이에요")
        try:
            stage = updater.download(info, config_mod.home())
        except Exception as e:
            raise HTTPException(502, f"업데이트를 받지 못했어요: {e}")
        updater.write_script(stage, config_mod.home())
        c.store.log("notice", "update", f"업데이트 {__version__} -> {info['latest']} 준비 완료, 다시 켭니다")
        threading.Timer(1.5, lambda: os._exit(updater.RESTART_CODE)).start()
        return {"ok": True, "latest": info["latest"]}

    @app.get("/api/market/sector-tree")
    def market_sector_tree():
        return guard(lambda: {"demo": toss() is None, "tree": market_mod.sector_tree(toss())})

    @app.get("/api/market/investors")
    def market_investors(side: str = "buy"):
        return guard(lambda: market_mod.investor_flows(toss(), side))

    @app.get("/api/sector/{sector_id}/stocks")
    def sector_stocks(sector_id: str, market: str = "KR"):
        t = toss()
        if t is None:
            return {"demo": True, "items": [{"symbol": s_, "name": n, "tradingAmount": None, "marketCap": None}
                                            for s_, n, _ in market_mod.DEMO_KR[:6]]}
        rows = guard(lambda: t.sector_stocks(sector_id, market))
        from ..toss import num
        return {"demo": False, "items": [{"symbol": x["symbol"], "name": x.get("name"), "tradingAmount": num(x.get("tradingAmount")),
                                          "marketCap": num(x.get("marketCapitalization"))} for x in rows[:30]]}

    @app.get("/api/stock/{symbol}/extra")
    def stock_extra(symbol: str):
        """국내 종목의 프로그램매매·공매도·신용거래 최근 20일."""
        t = toss()
        if t is None or not symbol[:1].isdigit():
            return {"program": [], "short": [], "credit": [], "lending": []}
        out = {}
        try:
            out["lending"] = market_mod.lending(t, symbol.upper(), 20)
        except Exception:
            out["lending"] = []
        for k, fn in (("program", t.program_trades), ("short", t.short_selling), ("credit", t.credit_trades)):
            try:
                out[k] = fn(symbol.upper(), 20).get("records", [])
            except Exception:
                out[k] = []
        return out

    # 계좌 요약·미체결
    @app.get("/api/account/overview")
    def account_overview():
        c = ctx()
        out = {"broker": c.broker.name, "markets": {}}
        for m in (Market.KR, Market.US):
            if not c.cfg["markets"][m.value]["enabled"]:
                continue
            try:
                a = c.broker.account(m)
            except Exception as e:
                out["markets"][m.value] = {"error": str(e)}
                continue
            cost = sum(p.qty * p.avg_price for p in a.positions.values())
            value = sum(p.value for p in a.positions.values())
            hist = c.store.equity_history(c.broker.name, m.value)
            base = hist[0]["equity"] if hist else (c.cfg["paper"]["initial_cash"].get(m.value) if c.broker.name == "paper" else None)
            prev = hist[-2]["equity"] if len(hist) >= 2 else base
            eq = a.equity
            dp = [p.day_pnl for p in a.positions.values() if p.day_pnl is not None]
            out["markets"][m.value] = {
                "day_pnl": sum(dp) if dp else None,  # 토스가 계산한 오늘 손익 (보유 종목)
                "equity": eq, "cash": a.cash, "invested": value, "cost": cost, "unrealized": value - cost,
                "unrealized_rate": (value / cost - 1) if cost else 0, "total_return": (eq / base - 1) if base else None,
                "daily_return": (eq / prev - 1) if prev else None, "positions": len(a.positions),
                "history": [{"time": h["trade_date"], "value": h["equity"]} for h in hist],
            }
        if c.broker.name == "toss":
            try:
                out["commissions"] = c.broker.client.commissions()
            except Exception:
                out["commissions"] = []
        return out

    @app.get("/api/account/open-orders")
    def open_orders():
        c = ctx()
        if c.broker.name != "toss":
            return {"items": [], "note": "미체결 주문 조회는 토스증권 실전 모드에서 됩니다"}
        return {"items": guard(lambda: c.broker.client.orders("OPEN"))}

    @app.post("/api/account/orders/{order_id}/cancel", dependencies=[Depends(need_token)])
    def cancel_order(order_id: str):
        c = ctx()
        if c.broker.name != "toss":
            raise HTTPException(400, "토스증권 실전 모드에서만 취소할 수 있습니다")
        r = guard(lambda: c.broker.client.cancel(order_id))
        c.store.log("warn", "web", f"주문 취소 요청 {order_id}")
        return r

    # 실현 손익·세금, 업종 쏠림
    @app.get("/api/account/realized")
    def account_realized(year: str = ""):
        c = ctx()
        fx = None
        try:
            t = toss()
            fx = float(t.exchange_rate().get("rate") or 0) or None if t else None
        except Exception:
            pass
        return extras.tax_summary(c.store, c.broker.name, year or __import__("datetime").date.today().strftime("%Y"), fx)

    @app.get("/api/account/concentration")
    def account_concentration():
        c = ctx()
        out = {}
        for m in (Market.KR, Market.US):
            if not c.cfg["markets"][m.value]["enabled"]:
                continue
            try:
                a = c.broker.account(m)
            except Exception:
                continue
            out[m.value] = extras.concentration([{"symbol": p.symbol, "value": p.value} for p in a.positions.values()],
                                                float(c.cfg["risk"]["max_weight"]), float(c.cfg.get("max_sector_weight", 0.4)), a.cash)
        return out

    @app.get("/api/report")
    def daily_report(market: str = "KR"):
        import json as _json
        from ..engine import trade_date
        c = ctx()
        today = trade_date(Market(market))
        saved = _json.loads(c.store.get(f"report_{market}") or "null")
        rep = saved if saved and saved.get("date") == today else extras.daily_report(c.store, c.broker.name, market, today)
        rep["final"] = bool(saved and saved.get("date") == today)  # 장 마감 뒤 확정본인지
        rep["orders"] = [o | {"name": name_of(o["symbol"])} for o in rep.get("orders", [])]
        rep["plans"] = [p | {"name": name_of(p["symbol"])} for p in rep.get("plans", [])]
        rep["history"] = [{"date": h["trade_date"], "equity": h["equity"]} for h in c.store.equity_history(c.broker.name, market)[-10:]]
        rep["by_symbol"] = [{"symbol": s, "name": name_of(s), "pnl": v} for s, v in (rep.get("realized_year") or {}).get("by_symbol", {}).items()]
        return rep

    # 알림
    @app.get("/api/notices")
    def notices(after: int = 0):
        c = ctx()
        from ..humanize import say
        return [e | {"message": say(e["message"], name_of)} for e in
                c.store.rows("select * from events where level='notice' and id>? order by id desc limit 30", (after,))]

    @app.post("/api/telegram/find-chat", dependencies=[Depends(need_token)])
    def telegram_find_chat():
        """내 봇 대화에서 대화방 번호를 찾아 저장하고, 확인 메시지를 보낸다."""
        from .. import checks
        try:
            r = checks.telegram_find_chat(store=Store(config_mod.home() / "qtrader.db"))
        except Exception as e:
            raise HTTPException(400, str(e))
        cfg = config_mod.load()
        cfg.setdefault("notify", {})["telegram_chat_id"] = r["chat_id"]
        config_mod.save(cfg)
        extras.notify(Store(config_mod.home() / "qtrader.db"), cfg, "QTrader 알림이 연결됐어요. 앞으로 체결·손절·하루 요약을 여기로 보내 드릴게요", "test")
        return r

    @app.post("/api/notify/test", dependencies=[Depends(need_token)])
    def notify_test():
        c = ctx()
        extras.notify(c.store, c.cfg, "알림 테스트입니다. 이 메시지가 보이면 설정이 끝났습니다", "test")
        return {"ok": True, "telegram": bool(vault.get("telegram_bot_token") and (c.cfg.get("notify") or {}).get("telegram_chat_id"))}

    @app.get("/api/alerts")
    def alert_list():
        return config_mod.load().get("alerts") or []

    @app.post("/api/alerts", dependencies=[Depends(need_token)])
    def alert_add(body: AlertIn):
        if body.op not in ("above", "below") or body.price <= 0 or not body.symbol.strip():
            raise HTTPException(400, "종목, 조건(이상/이하), 가격을 확인하세요")
        cfg = config_mod.load()
        al = cfg.get("alerts") or []
        if len(al) >= 30:
            raise HTTPException(400, "가격 알림은 30개까지 둘 수 있습니다")
        al.append({"symbol": body.symbol.strip().upper(), "name": body.name[:40], "op": body.op, "price": body.price})
        cfg["alerts"] = al
        config_mod.save(cfg)
        return al

    @app.delete("/api/alerts/{idx}", dependencies=[Depends(need_token)])
    def alert_del(idx: int):
        cfg = config_mod.load()
        al = cfg.get("alerts") or []
        if 0 <= idx < len(al):
            al.pop(idx)
        cfg["alerts"] = al
        config_mod.save(cfg)
        return al

    # 직접 주문 (종목 화면의 구매·판매)
    def _market_of(sym: str) -> Market:
        return Market.KR if sym[:1].isdigit() else Market.US

    @app.get("/api/order/info/{symbol}")
    def order_info(symbol: str):
        from ..backtest import Costs
        c = ctx()
        sym = symbol.upper()
        m = _market_of(sym)
        try:
            price = float(c.data.price(m, sym))
        except Exception:
            price = None
        try:
            a = c.broker.account(m)
            cash, pos = a.cash, a.positions.get(sym)
        except Exception as e:
            raise HTTPException(400, f"계좌를 불러오지 못했습니다: {e}")
        sellable = pos.qty if pos else 0
        if c.broker.name == "toss":
            try:
                cash = c.broker.client.buying_power("KRW" if m == Market.KR else "USD") or cash
                sellable = int(c.broker.client.sellable_quantity(sym))
            except Exception:
                pass
        co = Costs.default(m)
        return {"symbol": sym, "market": m.value, "price": price, "cash": cash, "qty": pos.qty if pos else 0,
                "avg_price": pos.avg_price if pos else None, "sellable": sellable, "fee": co.fee, "sell_tax": co.sell_tax,
                "max_order": c.cfg["risk"]["max_order_value"][m.value], "live": c.broker.live, "broker": c.broker.name,
                "mode": c.cfg["mode"], "market_order": m == Market.KR or c.broker.name == "paper",
                "kill_switch": c.kill}

    @app.post("/api/order", dependencies=[Depends(need_token)])
    def place_order(body: OrderIn):
        from ..engine import trade_date
        from ..models import OrderRequest, Side
        c = ctx()
        sym = body.symbol.strip().upper()
        m = _market_of(sym)
        if body.side not in ("buy", "sell") or body.qty <= 0 or body.price < 0:
            raise HTTPException(400, "구분·수량·가격을 확인하세요")
        if body.price == 0 and m == Market.US and c.broker.name != "paper":
            raise HTTPException(400, "미국 주식은 지정가로만 주문할 수 있습니다")
        try:
            ref = body.price or float(c.data.price(m, sym))
        except Exception:
            raise HTTPException(400, "현재가를 몰라 시장가 주문을 낼 수 없습니다. 지정가로 주문하세요")
        limit = float(c.cfg["risk"]["max_order_value"][m.value])
        if body.qty * ref > limit:
            raise HTTPException(400, f"1회 주문 한도({limit:,.0f}{'원' if m == Market.KR else '$'})를 넘습니다. 설정 → 고급 → 주문 한도에서 바꿀 수 있어요")
        if body.side == "buy" and (body.stop or body.take) and not (0 < body.stop < ref < body.take):
            raise HTTPException(400, "손절가는 지금 가격보다 낮게, 익절가는 높게 정해 주세요")
        if body.side == "sell":  # 토스 서버 손절·익절이 걸려 있으면 먼저 풀어서 두 번 팔리지 않게
            from ..guard import release
            release(c, m, sym)
        req = OrderRequest(m, sym, Side(body.side), int(body.qty), float(body.price), "manual", "직접 주문")
        r = c.broker.order(req)
        status = ("filled" if r.filled_qty else "submitted") if r.ok else "rejected"
        oid = c.store.add_order(trade_date=trade_date(m), account=c.broker.name, market=m.value, symbol=sym, side=body.side,
                                qty=body.qty, price=r.filled_price or body.price or ref, status=status,
                                broker_order_id=r.broker_order_id, strategy="manual", message=f"직접 주문 | {r.message}")
        if r.ok and body.side == "buy" and body.stop and body.take:
            import json as _json
            if status == "filled":
                c.store.save_plan(c.broker.name, m.value, sym, "manual", r.filled_price or ref, body.stop, body.take)
            else:  # 토스: 체결되면 그때 계획을 만든다
                c.store.set(f"manual_plan_{oid}", _json.dumps({"stop": body.stop, "take": body.take}))
        if r.ok and body.side == "sell":
            c.store.close_plan(c.broker.name, m.value, sym, "manual")
        c.store.log("info" if r.ok else "error", "manual", f"직접 {'매수' if body.side == 'buy' else '매도'} {sym} x{body.qty} -> {status} {r.message}")
        if not r.ok:
            raise HTTPException(400, r.message or "주문이 거부되었습니다")
        return {"ok": True, "status": status, "message": r.message, "price": r.filled_price or body.price or ref, "order_id": r.broker_order_id}

    @app.get("/api/picks")
    def picks(market: str = "KR", refresh: bool = False, stats: bool = False):
        """오늘 AI 회의 후보. 아직 없으면 지금 고른다 (숫자 규칙만 쓰고 AI 호출은 없다).
        stats=true 면 {items, excluded}: 규칙별로 몇 종목이 빠졌는지 같이 준다 (기본 응답은 예전처럼 목록)."""
        from ..engine import trade_date
        from ..screener import stats_key, todays
        c = ctx()
        m = Market(market)
        today = trade_date(m)
        if refresh:
            c.store.exec("delete from state where key in (?,?)", (f"picks_{m.value}_{today}", stats_key(m, today)))
        st: dict = {}
        rows = todays(c, m, today, max(config_mod.meetings_cap(c.cfg, m), 1), st)
        met = {r["symbol"]: r for r in c.store.ai_runs(200) if r["trade_date"] == today}
        items = [p | {"name": name_of(p["symbol"]), "met": bool(met.get(p["symbol"])),
                      "rating": (met.get(p["symbol"]) or {}).get("rating")} for p in rows]
        if stats and m == Market.KR:
            from .. import scan
            st = (st or {}) | {"scan": scan.status(c.store, m, today)}  # 국내 전체 종목 훑기: 끝났는지, 몇 종목째인지
        return {"items": items, "excluded": st or None} if stats else items

    # 가상 계좌 처음부터 다시 (가상매매일 때만)
    @app.post("/api/paper/reset", dependencies=[Depends(need_token)])
    def paper_reset(body: PaperResetIn):
        from .. import conditional
        c = ctx()
        if c.cfg.get("mode") != "paper" or c.broker.name != "paper":
            raise HTTPException(400, "가상매매 모드에서만 가상 계좌를 처음부터 다시 시작할 수 있어요")
        want = body.market.upper()
        if want not in ("KR", "US", "ALL"):
            raise HTTPException(400, "시장은 KR, US, ALL 중 하나예요")
        out = {}
        for m in (Market.KR, Market.US):
            if want not in ("ALL", m.value):
                continue
            cash = float(c.cfg["paper"]["initial_cash"].get(m.value) or 0)
            out[m.value] = c.store.reset_paper(c.broker.name, m.value, cash)
            for r in conditional._load(c.store):  # 가상 계좌에 걸어 둔 조건 주문도 끈다
                if r.get("broker") == c.broker.name and r.get("market") == m.value and r.get("status") == "watching":
                    conditional.cancel(c, r["id"])
            fmt = f"{cash:,.0f}원" if m == Market.KR else f"${cash:,.2f}"
            r = out[m.value]
            c.store.log("warn", "paper", f"{'국내' if m == Market.KR else '미국'} 가상 계좌를 처음부터 다시 시작: 현금 {fmt}, "
                        f"보유 {r['positions']}종목·주문 기록 {r['orders']}건 지움, 손절·익절 계획 {r['plans']}개 닫음")
        return {"ok": True, "markets": out}

    # 실시간 사무실 현황판
    @app.get("/api/ai/today")
    def ai_today(market: str = "KR", fresh: bool = False):
        """AI 사무실 '오늘 AI 회의' 판: 자동으로 무엇을 하는지, 후보마다 지금 단계 (AI 호출 없음)."""
        from .. import live
        from ..engine import trade_date
        from ..screener import todays
        c = ctx()
        m = Market(market)

        def build():
            todays(c, m, trade_date(m), max(config_mod.meetings_cap(c.cfg, m), 1))  # 후보가 아직 없으면 지금 고른다
            return live.today_plan(c, m, name_of)
        # 계좌·장 시간 확인에 토스 호출이 있어 국내↔미국 전환이 느렸다. 잠깐 담아 두고 뒤에서 새로 고친다
        if fresh:
            market_mod._cache.pop(f"ai_today:{m.value}", None)
        return market_mod.cached(f"ai_today:{m.value}", 4, build, stale=60)

    @app.get("/api/live")
    def live_board(market: str = "KR"):
        from .. import live
        c = ctx()
        return live.snapshot(c, Market(market), name_of)

    # 수익 리포트
    @app.get("/api/performance")
    def performance(market: str = "KR"):
        import math
        c = ctx()
        m = Market(market)
        try:
            prices = {s: p.last_price for s, p in c.broker.account(m).positions.items()}
        except Exception:
            prices = {}
        r = extras.performance(c.store, c.broker.name, m.value, prices)
        for t in r["trades"]:
            t["name"] = name_of(t["symbol"])
        pf = r["summary"]["profit_factor"]
        if pf is not None and math.isinf(pf):
            r["summary"]["profit_factor"] = None
            r["summary"]["no_loss"] = True
        base = c.cfg["paper"]["initial_cash"].get(m.value) if c.broker.name == "paper" else None
        hist = c.store.equity_history(c.broker.name, m.value)
        base = base or (hist[0]["equity"] if hist else None)
        r["summary"]["base"] = base
        r["summary"]["total_rate"] = r["summary"]["total"] / base if base else None
        return r

    # 주문 정정 (토스)
    @app.post("/api/account/orders/{order_id}/modify", dependencies=[Depends(need_token)])
    def modify_order(order_id: str, body: ModifyIn):
        c = ctx()
        if c.broker.name != "toss":
            raise HTTPException(400, "주문 정정은 토스증권 실전 모드에서 됩니다 (가상매매는 바로 체결돼요)")
        m = _market_of(body.symbol.upper()) if body.symbol else Market.KR
        if m == Market.KR and body.qty <= 0:
            raise HTTPException(400, "국내 주식은 정정할 수량을 넣어야 해요")
        r = guard(lambda: c.broker.client.modify(order_id, body.price or None, body.qty or None, m))
        c.store.log("info", "manual", f"주문 정정 {order_id}: 가격 {body.price or '시장가'} · 수량 {body.qty or '그대로'}")
        return r

    # 조건 주문 · 분할 매수/매도
    @app.get("/api/conditional")
    def cond_list():
        from .. import conditional
        c = ctx()
        return [r | {"name": name_of(r["symbol"])} for r in reversed(conditional.listing(c))]

    @app.post("/api/conditional", dependencies=[Depends(need_token)])
    def cond_create(body: CondIn):
        from .. import conditional
        c = ctx()
        try:
            return guard(lambda: conditional.create(c, body.symbol, body.side, body.qty, body.trigger, body.price, body.days, body.note,
                                                    body.then_stop))
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/conditional/{cid}/modify", dependencies=[Depends(need_token)])
    def cond_modify(cid: str, body: CondEdit):
        from .. import conditional
        c = ctx()
        try:
            return guard(lambda: conditional.modify(c, cid, body.trigger, body.price, body.qty))
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.delete("/api/conditional/{cid}", dependencies=[Depends(need_token)])
    def cond_cancel(cid: str):
        from .. import conditional
        guard(lambda: conditional.cancel(ctx(), cid))
        return {"ok": True}

    @app.post("/api/order/split", dependencies=[Depends(need_token)])
    def order_split(body: SplitIn):
        """첫 번째는 바로 주문, 나머지는 가격이 step% 씩 움직일 때 나가는 조건 주문으로."""
        from .. import conditional
        c = ctx()
        sym = body.symbol.upper()
        m = _market_of(sym)
        n = max(2, min(int(body.parts), 4))
        if body.qty < n:
            raise HTTPException(400, f"{n}번에 나누려면 최소 {n}주가 필요해요")
        ref = body.price or float(c.data.price(m, sym))
        each = [body.qty // n + (1 if i < body.qty % n else 0) for i in range(n)]
        first = place_order(OrderIn(symbol=sym, side=body.side, qty=each[0], price=body.price))
        sign = -1 if body.side == "buy" else 1
        conds = []
        for i in range(1, n):
            trig = ref * (1 + sign * body.step_pct / 100 * i)
            conds.append(conditional.create(c, sym, body.side, each[i], trig, trig if m == Market.US else 0, 30, f"분할 {i + 1}/{n}"))
        c.store.log("info", "manual", f"분할 {'매수' if body.side == 'buy' else '매도'} {sym} {body.qty}주를 {n}번에 ({body.step_pct}% 간격)")
        return {"first": first, "conditions": conds}

    # 손절·익절 계획 직접 정하기 (직접 산 종목도 자동으로 지켜 준다)
    @app.post("/api/plans", dependencies=[Depends(need_token)])
    def plan_set(body: PlanIn):
        c = ctx()
        sym = body.symbol.upper()
        m = _market_of(sym)
        pos = c.broker.account(m).positions.get(sym)
        if not pos:
            raise HTTPException(400, "가진 종목에만 손절·익절을 정할 수 있어요")
        px = pos.last_price or pos.avg_price
        if not (0 < body.stop < px < body.take):
            raise HTTPException(400, f"손절가는 지금 가격({px:,})보다 낮게, 익절가는 높게 정해 주세요")
        c.store.save_plan(c.broker.name, m.value, sym, "manual", pos.avg_price, body.stop, body.take)
        fmt = (lambda v: f"{v:,.0f}원") if m == Market.KR else (lambda v: f"${v:,.2f}")
        c.store.log("info", "manual", f"{sym} 손절 {fmt(body.stop)} · 익절 {fmt(body.take)} 로 정했습니다")
        try:  # 토스면 서버 보호(OCO)도 바로 다시 건다
            from ..guard import sync
            sync(c)
        except Exception as e:
            c.store.log("warn", "guard", f"토스 보호 걸기 실패: {e}")
        return {"ok": True}

    @app.delete("/api/plans/{symbol}", dependencies=[Depends(need_token)])
    def plan_del(symbol: str):
        c = ctx()
        sym = symbol.upper()
        c.store.close_plan(c.broker.name, _market_of(sym).value, sym, "user")
        from ..guard import release
        guard(lambda: release(c, _market_of(sym), sym))
        c.store.log("info", "manual", f"{sym} 손절·익절 감시를 껐습니다")
        return {"ok": True}

    # 키 연결 테스트 (주문은 내지 않는다)
    @app.post("/api/secrets/test", dependencies=[Depends(need_token)])
    def secret_test(body: TestIn):
        from .. import checks
        fn = checks.TESTS.get(body.kind)
        if not fn:
            raise HTTPException(400, "이 키는 연결 테스트를 지원하지 않아요")
        steps = fn(config_mod.load())
        return {"kind": body.kind, "ok": all(s["ok"] for s in steps), "steps": steps}

    # 지수 대비 성과: 내 계좌 수익률 vs 코스피 / S&P 500 (같은 날짜부터)
    @app.get("/api/benchmark")
    def benchmark(market: str = "KR"):
        c = ctx()
        hist = c.store.equity_history(c.broker.name, market)
        idx = market_mod.index_daily(toss(), market)
        name = "코스피" if market == "KR" else "S&P 500"
        if len(hist) < 2 or not idx:
            return {"name": name, "ours": [], "index": [], "note": "자산 기록이 이틀 이상 쌓이면 지수와 비교한 선이 그려져요" if len(hist) < 2
                    else f"{name} 시세를 받지 못했어요 (토스 키가 필요해요)" if market == "KR" else f"{name} 시세를 받지 못했어요"}
        start = hist[0]["trade_date"]
        base = hist[0]["equity"]
        ours = [{"time": h["trade_date"], "value": h["equity"] / base - 1} for h in hist]
        ix = [x for x in idx if x["time"] >= start]
        ib = ix[0]["close"] if ix else None
        index = [{"time": x["time"], "value": x["close"] / ib - 1} for x in ix] if ib else []
        diff = (ours[-1]["value"] - index[-1]["value"]) if index else None
        return {"name": name, "ours": ours, "index": index, "diff": diff}

    # 거래 기록 내보내기 (엑셀에서 바로 열리게 UTF-8 BOM)
    @app.get("/api/export/trades.csv")
    def export_trades(market: str = "KR"):
        import csv
        import io
        from fastapi.responses import Response
        c = ctx()
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["날짜", "시각(UTC)", "시장", "종목코드", "종목명", "구분", "수량", "가격", "금액", "상태", "방식", "근거"])
        kind = {"ai": "AI 자동", "ai-exit": "자동 손절·익절", "manual": "직접 주문"}
        for o in reversed(c.store.rows("select * from orders where account=? and market=? and status in ('filled','submitted') order by id desc limit 5000",
                                       (c.broker.name, market))):
            w.writerow([o["trade_date"], o.get("ts", ""), o["market"], o["symbol"], name_of(o["symbol"]), "매수" if o["side"] == "buy" else "매도",
                        o["qty"], o["price"], round(o["qty"] * o["price"], 2), {"filled": "체결", "submitted": "접수"}.get(o["status"], o["status"]),
                        kind.get(o.get("strategy") or "", o.get("strategy") or "규칙 전략"), (o.get("message") or "").split(" | ")[0]])
        w.writerow([])
        w.writerow(["실현 손익 (사고판 거래)"])
        w.writerow(["종목코드", "종목명", "산 날", "판 날", "수량", "산 가격", "판 가격", "손익", "수익률", "보유일", "판 이유"])
        for t in reversed(extras.performance(c.store, c.broker.name, market)["trades"]):
            w.writerow([t["symbol"], name_of(t["symbol"]), t["buy_date"], t["sell_date"], t["qty"], t["buy"], t["sell"], round(t["pnl"], 2),
                        f"{t['pct']:.2%}", t["days"], t["why"]])
        from datetime import date as _d
        fn = f"qtrader_{market}_{_d.today():%Y%m%d}.csv"
        return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f"attachment; filename={fn}"})

    # 실시간 시세: 2초마다 서버가 밀어 준다 (Server-Sent Events). 토스 Open API 에 WebSocket 이 없어서
    #   서버가 여러 화면의 종목을 한 번에 묶어 조회하고, 모든 화면이 그 결과를 나눠 쓴다.
    _px: dict = {"t": 0.0, "key": "", "data": {}}

    def _prices(symbols: list[str]) -> dict:
        """종목 현재가·등락률 + 지수(IDX:KOSPI) + 환율(FX). 토스 키가 없으면(체험용) 아무것도 보내지 않는다
        (화면마다 합성 시세가 달라 섞이면 헷갈림)."""
        import time as _t
        from ..toss import num
        key = ",".join(sorted(symbols))
        if key == _px["key"] and _t.time() - _px["t"] < 1.5:
            return _px["data"]
        t = toss()
        out: dict = {"prices": {}, "rates": {}, "idx": {}, "fx": None}
        if t is not None:
            stocks = [s for s in symbols if not s.startswith(("IDX:", "FX"))]
            idx = [s[4:] for s in symbols if s.startswith("IDX:")]
            try:
                got = t.quotes(stocks) if stocks else {}
                for s in stocks:
                    q = got.get(s) or got.get(s.split(":")[-1]) or {}
                    if q.get("price"):
                        out["prices"][s] = q["price"]
                        if q.get("change_rate") is not None:
                            out["rates"][s] = round(q["change_rate"], 5)
            except Exception:
                pass
            if idx:
                try:
                    for x in t.indicator_prices(idx):
                        if num(x.get("lastPrice")):
                            out["idx"][x["symbol"]] = num(x["lastPrice"])
                except Exception:
                    pass
            if "FX" in symbols:
                try:
                    f = market_mod.cached("fx_live", 5, t.exchange_rate) or {}
                    out["fx"] = {"rate": num(f.get("rate")) or None, "change": num(f.get("basisPoint"))}
                except Exception:
                    pass
        _px.update(t=_t.time(), key=key, data=out)
        return out

    @app.get("/api/stream")
    async def price_stream(symbols: str = ""):
        import asyncio
        import json as _json
        from fastapi.responses import StreamingResponse
        syms = [s for s in dict.fromkeys(x.strip().upper() for x in symbols.split(",")) if s][:100]

        async def gen():
            last: dict = {}
            yield "retry: 3000\n\n"
            while True:
                d = await asyncio.to_thread(_prices, syms) if syms else {}
                msg = {"ts": __import__("time").time()}
                for k in ("prices", "rates", "idx"):  # 바뀐 값만 보낸다
                    cur = d.get(k) or {}
                    prev = last.setdefault(k, {})
                    msg[k] = {s: v for s, v in cur.items() if v is not None and prev.get(s) != v}
                    prev.update(cur)
                if d.get("fx") and d["fx"] != last.get("fx"):
                    msg["fx"] = last["fx"] = d["fx"]
                yield f"data: {_json.dumps(msg)}\n\n"
                await asyncio.sleep(2)
        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # 전략 비교 (가상 계좌들)
    @app.get("/api/compare")
    def compare_view(market: str = "KR"):
        from .. import compare
        c = ctx()
        return {"enabled": bool((c.cfg.get("compare") or {}).get("enabled")), "rows": compare.summary(c, Market(market))}

    @app.post("/api/compare", dependencies=[Depends(need_token)])
    def compare_toggle(body: ToggleIn):
        cfg = config_mod.load()
        cfg.setdefault("compare", {"modes": ["auto", "long", "swing", "short"]})["enabled"] = body.on
        config_mod.save(cfg)
        return {"enabled": body.on}

    # 매매 이유 보고: 자동 주문 뒤 '왜, 무슨 근거로' 샀고 팔았는지
    @app.get("/api/trades/why")
    def trades_why(limit: int = 20):
        import json as _json
        c = ctx()
        rows = c.store.rows("select * from orders where account=? and status in ('filled','submitted') and side in ('buy','sell') "
                            "order by id desc limit ?", (c.broker.name, limit))
        out = []
        for o in rows:
            parts = (o.get("message") or "").split(" | ")
            item = {"id": o["id"], "date": o["trade_date"], "ts": o.get("ts"), "market": o["market"], "symbol": o["symbol"], "name": name_of(o["symbol"]),
                    "side": o["side"], "qty": o["qty"], "price": o["price"], "status": o["status"], "strategy": o.get("strategy"),
                    "summary": parts[0], "detail": parts[1:-1], "run": None}
            if o.get("strategy") == "ai" and o["side"] == "buy":
                r = c.store.rows("select id, final, reports from ai_runs where symbol=? and status='done' and trade_date<=? "
                                 "order by finished_at desc limit 1", (o["symbol"], o["trade_date"]))
                if r:
                    f = _json.loads(r[0]["final"] or "{}") or {}
                    rep = _json.loads(r[0]["reports"] or "{}") or {}
                    cl = rep.get("checklist") or {}
                    item["run"] = {"id": r[0]["id"], "name": rep.get("name"), "rating": f.get("rating"),
                                   "rating_ko": RATING_KO.get(f.get("rating") or "", ""), "confidence": f.get("confidence"),
                                   "headline": f.get("headline"), "reasons": (f.get("reasons") or [])[:4], "worry": f.get("worry"),
                                   "passed": [x["name"] for x in cl.get("rules", []) if x.get("ok")],
                                   "rules": len(cl.get("rules", [])), "plan": rep.get("plan")}
            out.append(item)
        return out

    # AI 비용·회의 비교
    @app.get("/api/ai/cost")
    def ai_cost():
        cfg = config_mod.load()
        return {"month_krw": extras.month_cost_krw(Store(config_mod.home() / "qtrader.db")),
                "cap_krw": float(cfg["ai"].get("monthly_cost_cap_krw") or 0)}

    @app.get("/api/ai/compare/{symbol}")
    def ai_compare(symbol: str, limit: int = 6):
        import json as _json
        c = ctx()
        out = []
        for r in c.store.ai_runs(limit, symbol.upper()):
            if r["status"] != "done":
                continue
            try:
                rep = _json.loads(r.get("reports") or "{}") if isinstance(r.get("reports"), str) else (r.get("reports") or {})
                fin = _json.loads(r.get("final") or "{}") if isinstance(r.get("final"), str) else (r.get("final") or {})
            except ValueError:
                rep, fin = {}, {}
            cl = rep.get("checklist") or {}
            out.append({"id": r["id"], "date": r["trade_date"], "rating": r.get("rating"), "rating_ko": RATING_KO.get(r.get("rating") or "", ""),
                        "confidence": r.get("confidence"), "plan": rep.get("plan"), "price": (rep.get("facts") or {}).get("price"),
                        "passed": cl.get("passed"), "total": cl.get("total"),
                        "summary": (fin.get("summary") or fin.get("thesis") or "")[:200], "cost_usd": rep.get("cost_usd")})
        return out

    # 시스템: 윈도우 시작 시 실행
    @app.get("/api/system")
    def system_info():
        return {"autostart": extras.autostart_on(), "windows": extras.startup_file() is not None}

    @app.post("/api/system/autostart", dependencies=[Depends(need_token)])
    def system_autostart(body: ToggleIn):
        try:
            return {"autostart": extras.set_autostart(body.on)}
        except (RuntimeError, OSError) as e:
            raise HTTPException(400, str(e))

    # 투자 성향
    @app.get("/api/profile")
    def get_profile():
        cfg = config_mod.load()
        return {"investor": cfg.get("investor"), "questions": profile_mod.QUESTIONS,
                "types": [{"name": n, "desc": d} for n, _, d in profile_mod.TYPES], "criteria": profile_mod.criteria(cfg)}

    @app.post("/api/profile", dependencies=[Depends(need_token)])
    def set_profile(body: ProfileIn):
        try:
            new, result = profile_mod.apply(config_mod.load(), body.answers)
        except (ValueError, KeyError) as e:
            raise HTTPException(400, str(e))
        config_mod.save(new)
        Store(config_mod.home() / "qtrader.db").log("info", "profile", f"투자 성향 {result['type']} ({result['style']}) 적용")
        return result

    @app.post("/api/profile/skip", dependencies=[Depends(need_token)])
    def skip_profile():
        cfg = config_mod.load()
        cfg["investor"] = {"type": None, "skipped": True}
        config_mod.save(cfg)
        return {"ok": True}

    @app.get("/api/news")
    def news_feed():
        return news_mod.feeds(config_mod.load().get("news", {}).get("feeds"))

    @app.get("/api/principles")
    def principle_list():
        return {"references": [{"key": k, "author": a, "title": t, "venue": v, "finding": f, "applied": u}
                               for k, a, t, v, f, u in principles.REFERENCES],
                "rules": principles.DEFAULT_RULES | (config_mod.load()["ai"].get("rules") or {})}

    @app.get("/api/plans")
    def plans():
        c = ctx()
        return [p | {"name": name_of(p["symbol"])} for p in c.store.active_plans(c.broker.name)]

    # AI 투자 회의
    @app.get("/api/ai/roles")
    def ai_roles():
        return {"roles": [{"id": r.id, "name": r.name, "title": r.title, "team": r.team, "look": r.look, "web": r.web,
                           "deep": r.deep} for r in ROLES], "stages": [{"key": k, "name": n} for k, n in STAGES]}

    @app.get("/api/ai/runs")
    def ai_runs(limit: int = 30):
        live = meeting.live_runs()
        ids = {r["id"] for r in live}
        hist = [r for r in ctx().store.ai_runs(limit) if r["id"] not in ids]
        rows = live + hist
        for r in rows:
            f = r.get("final") or {}
            r["rating_ko"] = RATING_KO.get(f.get("rating"), "")
        return rows[:limit]

    @app.get("/api/ai/run/{run_id}")
    def ai_run(run_id: str):
        r = meeting.get_run(run_id)
        if r is None:
            rows = [x for x in ctx().store.ai_runs(500) if x["id"] == run_id]
            if not rows:
                raise HTTPException(404, "회의를 찾을 수 없습니다")
            r = rows[0]
        return r

    @app.get("/api/ai/chart/{symbol}")
    def ai_chart(symbol: str, days: int = 180):
        """분석 결과 화면용: 일봉, 이동평균, 지지·저항."""
        c = ctx()
        s = symbol.upper()
        try:
            df = c.data.bars(market_of(s), s, max(days, 130))
        except Exception as e:
            if toss() is not None:
                raise HTTPException(502, f"차트 데이터를 받지 못했습니다: {e}")
            # 키 없는 체험: 종목 화면과 같은 체험 시세로 (파일 경로 같은 오류를 보이지 않게)
            import pandas as pd
            rows = stockdata.candles(None, s, "1d", max(days, 130))["candles"]
            df = pd.DataFrame(rows)
            df.index = pd.to_datetime(df.pop("time"))
        cl = df["close"]
        out = {"candles": [], "sma20": [], "sma60": [], "sma120": [], "levels": key_levels(df)}
        smas = {k: ind.sma(cl, k) for k in (20, 60, 120)}
        tail = df.tail(days)
        for d, r in tail.iterrows():
            t = d.strftime("%Y-%m-%d")
            out["candles"].append({"time": t, "open": r.open, "high": r.high, "low": r.low, "close": r.close})
            for k in (20, 60, 120):
                v = smas[k].get(d)
                if v == v and v is not None:
                    out[f"sma{k}"].append({"time": t, "value": round(float(v), 2)})
        out["rsi"] = round(float(ind.rsi(cl).iloc[-1]), 1)
        return out

    @app.get("/api/ai/track")
    def ai_track():
        c = ctx()
        runs = c.store.ai_runs(300)
        return track.evaluate(runs, lambda s: c.data.bars(market_of(s), s, 260))

    @app.get("/api/approvals")
    def approvals(status: str | None = None):
        return ctx().store.approvals(status, 100)

    @app.post("/api/approvals/{aid}", dependencies=[Depends(need_token)])
    def decide(aid: int, body: DecideIn):
        c = ctx()
        if not body.approve:
            c.store.decide_approval(aid, "rejected", "사용자 거절")
            c.store.log("info", "approval", f"승인 #{aid} 거절")
            return {"id": aid, "status": "rejected"}
        try:
            return execute_approval(c, aid)
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/ai/meeting", dependencies=[Depends(need_token)])
    def ai_meeting(body: MeetingIn):
        c = ctx()
        if llm is None and not vault.get_anthropic():
            import os
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise HTTPException(400, "설정에서 Claude API 키를 먼저 등록하세요")
        t = toss()
        # 'SK하이닉'처럼 이름으로 써도 종목 코드로 바꾼다 (이름을 그대로 토스에 보내면 400 invalid-request)
        sym = stockdata.resolve(stockdata.master(t, config_mod.home()), body.symbol)
        if not sym:
            raise HTTPException(400, f"'{body.symbol.strip()}' 종목을 찾지 못했어요. 검색에서 종목을 골라 주세요")
        try:
            return meeting.start(sym, c.data, c.store, c.cfg, llm=llm, toss=t)
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/assistant/stream", dependencies=[Depends(need_token)])
    def chat_stream(body: ChatIn):
        """큐비 답을 쓰는 대로 보낸다 (줄마다 'data: {json}')."""
        import json as _json
        from fastapi.responses import StreamingResponse
        if not body.text.strip():
            raise HTTPException(400, "메시지를 입력하세요")

        def gen():
            for ev in assistant.chat_stream(body.thread_id, body.text.strip()[:4000], body.page[:300]):
                yield f"data: {_json.dumps(ev, ensure_ascii=False)}\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/assistant", dependencies=[Depends(need_token)])
    def chat(body: ChatIn):
        if not body.text.strip():
            raise HTTPException(400, "메시지를 입력하세요")
        try:
            return assistant.chat(body.thread_id, body.text.strip()[:4000], body.page[:300])
        except LLMError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/backtest", dependencies=[Depends(need_token)])
    def run_backtest(body: BacktestIn):
        c = ctx()
        m = Market(body.market)
        uni = body.universe or c.cfg["markets"][m.value]["universe"]
        errors: list[str] = []
        p = panel(c.data, m, uni, body.days, errors)
        if not p:
            raise HTTPException(400, "데이터가 없습니다: " + "; ".join(errors[:3]))
        try:
            s = strategies.build(body.strategy, body.params)
        except (ValueError, TypeError) as e:
            raise HTTPException(400, str(e))
        r = c.cfg["risk"]
        res = backtest.run(s, p, m, max_weight=r["max_weight"], max_exposure=r["max_exposure"])
        out = res.to_dict()
        out["data_errors"] = errors
        out["params"] = s.params()
        return out

    @app.post("/api/run-once", dependencies=[Depends(need_token)])
    def run_once(body: RunIn):
        c = ctx()
        if c.broker.live and not body.dry_run:
            c.store.log("warn", "web", "실전 계좌 즉시 실행 요청")
        return run_market(c, Market(body.market), dry_run=body.dry_run, force=True)

    @app.post("/api/kill-switch", dependencies=[Depends(need_token)])
    def kill(body: ToggleIn):
        c = ctx()
        c.store.set("kill_switch", "1" if body.on else "0")
        if body.on:
            from datetime import datetime as _dt, timezone as _tz
            c.store.set("kill_since", _dt.now(_tz.utc).isoformat(timespec="seconds"))
            c.store.set("kill_reason", "직접 켬")
        c.store.log("warn" if body.on else "info", "web", f"킬 스위치 {'켬' if body.on else '끔'}")
        return {"kill_switch": body.on}

    @app.post("/api/auto", dependencies=[Depends(need_token)])
    def auto(body: ToggleIn):
        c = ctx()
        if body.on and not (c.cfg.get("investor") or {}).get("type"):
            raise HTTPException(400, "투자 성향 검사를 먼저 해 주세요. 결과에 맞춰 비중·손실 한도·매수 조건이 정해집니다")
        c.store.set("auto_trading", "1" if body.on else "0")
        c.store.log("info", "web", f"자동매매 {'시작' if body.on else '중지'} ({c.broker.name})")
        return {"auto_trading": body.on}

    @app.get("/api/config")
    def get_config():
        return config_mod.load()

    @app.put("/api/config", dependencies=[Depends(need_token)])
    def put_config(cfg: dict):
        merged = config_mod._merge(config_mod.DEFAULT, cfg)
        errs = config_mod.validate(merged)
        for m, mc in merged["markets"].items():
            if mc["strategy"] != "ai" and mc["strategy"] not in strategies.REGISTRY:
                errs.append(f"{m}: 알 수 없는 전략 {mc['strategy']}")
        if errs:
            raise HTTPException(400, " / ".join(errs))
        old = config_mod.load()
        config_mod.save(merged)
        if old["mode"] != merged["mode"]:
            # 모드를 바꾸면 자동매매는 꺼진 상태로 시작
            store = Store(config_mod.home() / "qtrader.db")
            store.set("auto_trading", "0")
            store.log("info", "web", f"모드 변경 {old['mode']} -> {merged['mode']}. 자동매매를 껐습니다")
        return merged

    @app.put("/api/secrets", dependencies=[Depends(need_token)])
    def put_secret(body: SecretIn):
        if body.kind not in SECRET_KINDS:
            raise HTTPException(400, f"kind 는 {SECRET_KINDS} 중 하나")
        if len(body.key.strip()) < 10 or (body.kind not in ("anthropic", "dart", "telegram") and len(body.secret.strip()) < 10):
            raise HTTPException(400, "키가 너무 짧습니다")
        if body.kind.startswith("kis"):
            vault.set_kis(body.kind[4:], body.key, body.secret)
        elif body.kind == "toss":
            vault.set_toss(body.key, body.secret)
        elif body.kind == "dart":
            vault.put("dart_api_key", body.key)
        elif body.kind == "telegram":
            vault.put("telegram_bot_token", body.key)
        else:
            vault.put("anthropic_api_key", body.key)
        return {"ok": True, "keys": key_state()}

    @app.delete("/api/secrets/{kind}", dependencies=[Depends(need_token)])
    def del_secret(kind: str):
        if kind not in SECRET_KINDS:
            raise HTTPException(400, "알 수 없는 키 종류")
        if kind.startswith("kis"):
            vault.delete_kis(kind[4:])
        elif kind == "toss":
            vault.delete_toss()
        elif kind == "dart":
            vault.delete("dart_api_key")
        elif kind == "telegram":
            vault.delete("telegram_bot_token")
        else:
            vault.delete("anthropic_api_key")
        return {"ok": True, "keys": key_state()}

    return app
