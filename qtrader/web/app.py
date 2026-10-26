"""로컬 대시보드 서버. 127.0.0.1 에서만 열리고, 변경 요청은 실행마다 새로 만드는 토큰이 있어야 한다."""
from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import __version__, backtest, config as config_mod, fundamentals, market as market_mod, news as news_mod, principles, stockdata, strategies, vault
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
SECRET_KINDS = ("kis_demo", "kis_real", "toss", "anthropic", "dart")


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


class SecretIn(BaseModel):
    kind: str
    key: str
    secret: str = ""


class MeetingIn(BaseModel):
    symbol: str


class DecideIn(BaseModel):
    approve: bool


class ChatIn(BaseModel):
    text: str
    thread_id: str | None = None


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
            "dart": bool(vault.get("dart_api_key"))}


def create_app(scheduler: Scheduler | None = None, llm=None, assistant_client=None) -> FastAPI:
    app = FastAPI(title="QTrader", version=__version__, docs_url=None, redoc_url=None)
    token = session_token()
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

    def ctx():
        try:
            return build_context()
        except RuntimeError as e:
            raise HTTPException(400, str(e))

    def toss():
        return toss_client(config_mod.load())

    assistant = Assistant(ctx, toss, config_mod.load, client=assistant_client)

    @app.get("/", response_class=HTMLResponse)
    def index():
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        return html.replace("__TOKEN__", token).replace("__VERSION__", __version__)

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
            "auto_trading": bool(st and st.get("auto_trading", "0") == "1"),
            "keys": key_state(), "markets": markets, "error": err, "scheduler_error": sched.last_error,
        }

    @app.get("/api/positions")
    def positions():
        c = ctx()
        out = []
        for m in (Market.KR, Market.US):
            if not c.cfg["markets"][m.value]["enabled"]:
                continue
            a = c.broker.account(m)
            for p in a.positions.values():
                pnl = (p.last_price / p.avg_price - 1) if p.avg_price else 0
                out.append({"market": m.value, "symbol": p.symbol, "qty": p.qty, "avg_price": p.avg_price,
                            "last_price": p.last_price, "value": p.value, "pnl": round(pnl, 4)})
        return out

    @app.get("/api/orders")
    def orders(limit: int = 100):
        return ctx().store.orders(limit)

    @app.get("/api/events")
    def events(limit: int = 100):
        return ctx().store.events(limit)

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

    @app.get("/api/market/rankings")
    def market_rankings(kind: str = "amount", market: str = "KR", duration: str = "realtime", count: int = 30):
        if market not in ("KR", "US"):
            raise HTTPException(400, "market 은 KR 또는 US")
        try:
            return market_mod.rankings(toss(), kind, market, duration, min(max(count, 1), 100))
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            raise HTTPException(502, f"순위를 받지 못했습니다: {e}")

    @app.get("/api/market/watch")
    def market_watch():
        try:
            return market_mod.watch_quotes(toss(), config_mod.load().get("watchlist", []))
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
        s = symbol.upper().split(":")[-1]
        cfg = config_mod.load()
        try:
            if s[:1].isdigit():
                key = vault.get("dart_api_key")
                if not key:
                    raise fundamentals.FundamentalsError("국내 재무제표는 설정에서 DART 오픈API 인증키(무료)를 등록하면 볼 수 있습니다")
                fin = fundamentals.dart_financials(s, key, config_mod.home())
            else:
                fin = fundamentals.sec_financials(s, cfg.get("sec_contact", ""))
        except fundamentals.FundamentalsError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": f"재무제표를 받지 못했습니다: {e}"}
        sm = stockdata.summary(toss(), symbol)
        return fin | {"ratios": fundamentals.ratios(fin, sm["price"], sm["shares"])}

    @app.get("/api/stock/{symbol}/news")
    def stock_news(symbol: str, name: str = ""):
        q = name or symbol
        try:
            return {"items": news_mod.stock_news(f"{q} 주가")}
        except Exception as e:
            return {"items": [], "error": str(e)}

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
        return c.store.active_plans(c.broker.name)

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
            raise HTTPException(502, f"차트 데이터를 받지 못했습니다: {e}")
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
        try:
            return meeting.start(body.symbol, c.data, c.store, c.cfg, llm=llm, toss=toss())
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/assistant", dependencies=[Depends(need_token)])
    def chat(body: ChatIn):
        if not body.text.strip():
            raise HTTPException(400, "메시지를 입력하세요")
        try:
            return assistant.chat(body.thread_id, body.text.strip()[:4000])
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
        c.store.log("warn" if body.on else "info", "web", f"킬 스위치 {'켬' if body.on else '끔'}")
        return {"kill_switch": body.on}

    @app.post("/api/auto", dependencies=[Depends(need_token)])
    def auto(body: ToggleIn):
        c = ctx()
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
        if len(body.key.strip()) < 10 or (body.kind not in ("anthropic", "dart") and len(body.secret.strip()) < 10):
            raise HTTPException(400, "키가 너무 짧습니다")
        if body.kind.startswith("kis"):
            vault.set_kis(body.kind[4:], body.key, body.secret)
        elif body.kind == "toss":
            vault.set_toss(body.key, body.secret)
        elif body.kind == "dart":
            vault.put("dart_api_key", body.key)
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
        else:
            vault.delete("anthropic_api_key")
        return {"ok": True, "keys": key_state()}

    return app
