"""토스 서버 손절·익절 보호(OCO), OTO 조건주문, 조건주문 수정·목록 동기화, 종목 기본 정보 거르기."""
import pytest

from qtrader import conditional, config, guard
from qtrader.engine import Context, check_exits
from qtrader.models import Account, Market, OrderResult, Position
from qtrader.storage import Store


class Client:
    def __init__(self):
        self.calls, self.open = [], {}

    def _call(self, method, path, group, params=None, body=None, account=False):
        self.calls.append((method, path, group, body))
        if method == "POST" and path.endswith("/conditional-orders"):
            cid = f"C{len(self.calls)}"
            self.open[cid] = {"conditionalOrderId": cid, "symbol": body["symbol"], "type": body["type"], "quantity": body["quantity"],
                              "first": body["first"], "expireDate": body["expireDate"]}
            return {"conditionalOrderId": cid}
        if method == "POST" and path.endswith("/modify"):
            old = path.split("/")[-2]
            self.open.pop(old, None)
            self.open["N" + old] = {"conditionalOrderId": "N" + old, "symbol": "005930", "first": body["first"], "quantity": body["quantity"]}
            return {"conditionalOrderId": "N" + old}
        if method == "DELETE":
            self.open.pop(path.split("/")[-1], None)
            return {}
        if method == "GET" and path.endswith("/conditional-orders"):
            return {"conditionalOrders": list(self.open.values())}
        return {}


class Broker:
    name, live = "toss", True

    def __init__(self):
        self.client = Client()
        self.positions = {"005930": Position(Market.KR, "005930", 10, 70000, 72000)}
        self.sent = []

    def account(self, m):
        return Account(m, 1_000_000, {k: v for k, v in self.positions.items() if v.market == m})

    def order(self, req):
        self.sent.append(req)
        return OrderResult(True, "O1", "접수")


class Data:
    name = "fake"

    def price(self, m, s):
        return 72000.0


@pytest.fixture
def ctx(tmp_path):
    cfg = config.load()
    cfg["markets"]["US"]["enabled"] = False
    return Context(cfg, Store(tmp_path / "s.db"), Data(), Broker())


def test_guard_places_oco_and_releases(ctx):
    ctx.store.save_plan("toss", "KR", "005930", "r1", 70000, 66000, 80000)
    assert guard.sync(ctx) == ["005930"]
    m, path, grp, body = ctx.broker.client.calls[-1]
    assert (m, grp, body["type"]) == ("POST", "CONDITIONAL_ORDER", "OCO")
    assert body["first"]["orderSide"] == body["second"]["orderSide"] == "SELL"
    assert float(body["first"]["triggerPrice"]) == 80000 > 72000 > float(body["second"]["triggerPrice"]) == 66000
    assert guard.active(ctx, Market.KR, "005930")
    assert guard.sync(ctx) == []  # 이미 걸려 있으면 그대로
    ctx.store.close_plan("toss", "KR", "005930", "manual")
    assert guard.sync(ctx) == ["005930"] and not guard.active(ctx, Market.KR, "005930")
    assert ctx.broker.client.calls[-1][0] == "DELETE"


def test_guard_reissues_when_qty_changes(ctx):
    ctx.store.save_plan("toss", "KR", "005930", "r1", 70000, 66000, 80000)
    guard.sync(ctx)
    ctx.broker.positions["005930"].qty = 15
    assert guard.sync(ctx) == ["005930"]
    assert ctx.broker.client.calls[-1][3]["quantity"] == "15"


def test_check_exits_leaves_stop_to_toss_when_guarded(ctx, monkeypatch):
    ctx.store.save_plan("toss", "KR", "005930", "r1", 70000, 73000, 90000)  # 현재가 72000 < 손절가 -> 손절 신호
    ctx.store.set(guard.KEY, '{"KR:005930": {"id": "C1", "qty": 10, "stop": 73000, "take": 90000}}')
    monkeypatch.setattr("qtrader.scheduler.is_open", lambda m: True)
    check_exits(ctx, Market.KR)
    assert not ctx.broker.sent  # 정규장: 토스 OCO 가 판다
    monkeypatch.setattr("qtrader.scheduler.is_open", lambda m: False)
    check_exits(ctx, Market.KR)
    assert ctx.broker.sent and not guard.active(ctx, Market.KR, "005930")  # 시간외: 보호를 풀고 QTrader 가 판다


def test_oto_and_modify_and_external_listing(ctx):
    r = conditional.create(ctx, "005930", "buy", 3, 70000, then_stop=66500)
    body = ctx.broker.client.calls[-1][3]
    assert body["type"] == "OTO" and body["second"]["orderSide"] == "SELL" and body["orderType"] == "LIMIT"
    with pytest.raises(ValueError):
        conditional.create(ctx, "005930", "buy", 3, 70000, then_stop=71000)
    m = conditional.modify(ctx, r["id"], 69000, qty=4)
    assert m["toss_id"].startswith("N") and m["qty"] == 4
    ctx.broker.client.open["EXT"] = {"conditionalOrderId": "EXT", "symbol": "000660", "type": "SINGLE", "quantity": "1",
                                     "first": {"orderSide": "BUY", "triggerPrice": "150000"}}
    rows = conditional.listing(ctx)
    ext = [x for x in rows if x.get("external")]
    assert ext and ext[0]["symbol"] == "000660" and ext[0]["trigger"] == 150000
    assert any(x["id"] == r["id"] and x["status"] == "watching" for x in rows)


def test_paper_oto_chains_stop(isolated_home):
    from qtrader.data import write_demo_csv
    from qtrader.engine import build_context
    write_demo_csv(isolated_home / "data", config.load())
    c = build_context()
    px = c.data.price(Market.KR, "005930")
    r = conditional.create(c, "005930", "buy", 1, px * 1.05, then_stop=1)  # 바로 닿는 가격
    fired = conditional.check(c)
    assert fired and fired[0]["id"] == r["id"]
    nxt = [x for x in conditional._load(c.store) if x["status"] == "watching"]
    assert nxt and nxt[0]["side"] == "sell" and nxt[0]["dir"] == "down" and nxt[0]["trigger"] == 1


def test_basic_filter_drops_bad_listings(ctx):
    from datetime import date
    from qtrader import screener

    class T:
        def stocks(self, syms):
            return [{"symbol": "A", "status": "ACTIVE", "isCommonShare": True, "listDate": "2010-01-01"},
                    {"symbol": "B", "status": "ACTIVE", "leverageFactor": "2", "listDate": "2010-01-01"},
                    {"symbol": "C", "status": "ACTIVE", "isCommonShare": False, "listDate": "2010-01-01"},
                    {"symbol": "D", "status": "ACTIVE", "koreanMarketDetail": {"krxTradingSuspended": True}},
                    {"symbol": "E", "status": "ACTIVE", "listDate": date.today().isoformat()}]

    ctx.data.name, ctx.data.client = "toss", T()
    rows = [{"symbol": s, "held": False} for s in "ABCDE"] + [{"symbol": "Z", "held": True}]
    assert [r["symbol"] for r in screener._basic_filter(ctx, Market.KR, rows)] == ["A", "Z"]


def test_guard_notices_toss_sold(ctx):
    ctx.store.save_plan("toss", "KR", "005930", "r1", 70000, 66000, 80000)
    guard.sync(ctx)
    ctx.broker.positions.clear()  # 토스가 OCO 로 팔았다
    assert guard.sync(ctx) == ["005930"]
    assert not ctx.store.active_plans("toss") and not guard.active(ctx, Market.KR, "005930")
