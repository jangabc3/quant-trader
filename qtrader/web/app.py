"""로컬 대시보드 서버. 127.0.0.1 에서만 열리고, 변경 요청은 실행마다 새로 만드는 토큰이 있어야 한다."""
from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import __version__, backtest, config as config_mod, strategies, vault
from ..data import panel
from ..engine import build_context, run_market
from ..models import Market
from ..scheduler import Scheduler, is_open, next_run
from ..storage import Store

STATIC = Path(__file__).parent / "static"
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}


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


class SecretsIn(BaseModel):
    env: str
    app_key: str
    app_secret: str


def create_app(scheduler: Scheduler | None = None) -> FastAPI:
    app = FastAPI(title="QTrader", version=__version__, docs_url=None, redoc_url=None)
    token = secrets.token_urlsafe(24)
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

    @app.get("/", response_class=HTMLResponse)
    def index():
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        return html.replace("__TOKEN__", token).replace("__VERSION__", __version__)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/api/status")
    def status():
        cfg = config_mod.load()
        store_ctx = None
        err = ""
        try:
            store_ctx = build_context(cfg)
        except RuntimeError as e:
            err = str(e)
        markets = {}
        for m in (Market.KR, Market.US):
            mc = cfg["markets"][m.value]
            info = {"enabled": mc["enabled"], "strategy": mc["strategy"], "run_time": mc["run_time"],
                    "open": is_open(m), "next_run": next_run(m, mc["run_time"]).isoformat(timespec="minutes"),
                    "budget": mc["budget"]}
            if store_ctx and mc["enabled"]:
                try:
                    a = store_ctx.broker.account(m)
                    info.update(cash=a.cash, equity=a.equity, positions=len(a.positions))
                except Exception as e:
                    info["account_error"] = str(e)
            markets[m.value] = info
        st = store_ctx.store if store_ctx else None
        return {
            "version": __version__, "mode": cfg["mode"], "live_confirmed": cfg.get("live_confirmed", False),
            "broker": store_ctx.broker.name if store_ctx else None,
            "data_source": store_ctx.data.name if store_ctx else None,
            "kill_switch": bool(st and st.get("kill_switch", "0") == "1"),
            "auto_trading": bool(st and st.get("auto_trading", "0") == "1"),
            "keys": {"demo": vault.has_kis("demo"), "real": vault.has_kis("real")},
            "markets": markets, "error": err, "scheduler_error": sched.last_error,
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
            # 실전 즉시 실행은 화면에서 한 번 더 확인한 뒤에만 오도록 프론트에서 막지만, 서버도 기록한다
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
    def put_secrets(body: SecretsIn):
        if body.env not in ("demo", "real"):
            raise HTTPException(400, "env 는 demo 또는 real")
        if len(body.app_key) < 10 or len(body.app_secret) < 10:
            raise HTTPException(400, "앱키/시크릿이 너무 짧습니다")
        vault.set_kis(body.env, body.app_key, body.app_secret)
        return {"ok": True}

    @app.delete("/api/secrets/{env}", dependencies=[Depends(need_token)])
    def del_secrets(env: str):
        vault.delete_kis(env)
        return {"ok": True}

    return app
