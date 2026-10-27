"""토스증권 Open API 클라이언트 (국내·미국 주식).

명세: https://openapi.tossinvest.com/openapi-docs/latest/openapi.json (v1.2.24 기준)
- 토큰: POST /oauth2/token (form-urlencoded, client_credentials). 클라이언트당 유효 토큰 1개라
  재발급하면 이전 토큰이 바로 무효가 된다 -> 파일에 캐시해 프로세스끼리 공유한다.
- 계좌·주문 API 는 X-Tossinvest-Account: <accountSeq> 헤더가 필요하다.
- 주문은 토스증권 WTS 설정 > Open API > 허용 IP 에 이 PC 의 공인 IP 가 등록돼 있어야 한다.
- 모의투자 환경은 없다. 연습은 QTrader 가상매매(paper) 모드로 한다.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

from .models import Account, Market, OrderRequest, OrderResult, Position, Side

BASE = "https://openapi.tossinvest.com"
KST = timezone(timedelta(hours=9))

# 초당 호출 한도(명세의 Rate Limits Group). 여유를 두고 간격을 잡는다
GROUP_INTERVAL = {
    "AUTH": 0.25, "MARKET_DATA": 0.08, "MARKET_DATA_CHART": 0.06, "ACCOUNT": 1.05, "ASSET": 0.25,
    "ORDER": 0.12, "ORDER_INFO": 0.35, "ORDER_HISTORY": 0.25, "RANKING": 0.25, "MARKET_INDICATOR": 0.25,
    "MARKET_INFO": 0.25, "STOCK": 0.25, "STOCK_TRADING_TREND": 0.25,
}

INDICATORS = ["KOSPI", "KOSDAQ", "KR_BOND_3Y", "KR_BOND_10Y"]


class TossError(RuntimeError):
    def __init__(self, status: int, code: str, message: str, data=None):
        super().__init__(f"토스증권 {status} {code}: {message}".strip())
        self.status, self.code, self.data = status, code, data


def toss_symbol(symbol: str) -> str:
    """QTrader 의 'NYSE:KO' 표기를 토스증권 티커 'KO' 로."""
    return symbol.split(":", 1)[1].upper() if ":" in symbol else symbol.upper()


def num(v, default: float = 0.0) -> float:
    try:
        return float(v) if v not in (None, "") else default
    except (TypeError, ValueError):
        return default


class Toss:
    def __init__(self, client_id: str, client_secret: str, token_dir: Path, account_seq: int | None = None,
                 session: requests.Session | None = None, base: str = BASE):
        self.client_id, self.secret, self.base = client_id, client_secret, base
        self.http = session or requests.Session()
        self.token_file = Path(token_dir) / f"toss_token_{client_id[-8:]}.json"
        self._token: str | None = None
        self._expires = datetime.min
        self._account_seq = account_seq
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    # 인증
    def token(self, force: bool = False) -> str:
        if not force and self._token and datetime.now() < self._expires:
            return self._token
        if not force and self.token_file.exists():
            try:
                d = json.loads(self.token_file.read_text())
                exp = datetime.fromisoformat(d["expires"])
                if datetime.now() < exp:
                    self._token, self._expires = d["token"], exp
                    return self._token
            except (ValueError, KeyError):
                pass
        self._pace("AUTH")
        r = self.http.post(f"{self.base}/oauth2/token", timeout=10, data={
            "grant_type": "client_credentials", "client_id": self.client_id, "client_secret": self.secret})
        try:
            body = r.json()
        except ValueError:
            raise TossError(r.status_code, "token", r.text[:200])
        if "access_token" not in body:
            raise TossError(r.status_code, body.get("error", "token"), body.get("error_description", "토큰 발급 실패"))
        self._token = body["access_token"]
        self._expires = datetime.now() + timedelta(seconds=int(body.get("expires_in", 86400)) - 300)
        self.token_file.write_text(json.dumps({"token": self._token, "expires": self._expires.isoformat()}))
        return self._token

    def _pace(self, group: str) -> None:
        gap = GROUP_INTERVAL.get(group, 0.2)
        with self._lock:
            wait = gap - (time.monotonic() - self._last.get(group, 0.0))
            if wait > 0:
                time.sleep(wait)
            self._last[group] = time.monotonic()

    def _call(self, method: str, path: str, group: str, params: dict | None = None, body: dict | None = None,
              account: bool = False, _retry: int = 0):
        self._pace(group)
        headers = {"Authorization": f"Bearer {self.token()}", "Accept": "application/json"}
        if account:
            headers["X-Tossinvest-Account"] = str(self.account_seq())
        r = self.http.request(method, self.base + path, params=params, json=body, headers=headers, timeout=10)
        if r.status_code == 429 and _retry < 3:
            time.sleep(min(float(r.headers.get("Retry-After", "1") or 1), 5))
            return self._call(method, path, group, params, body, account, _retry + 1)
        if r.status_code == 401 and _retry == 0:
            self.token(force=True)  # 다른 곳에서 재발급해 무효가 된 토큰
            return self._call(method, path, group, params, body, account, 1)
        if r.status_code == 401:
            raise TossError(401, "unauthorized", "토스증권 인증이 끊겼습니다. 같은 Client ID 로 다른 프로그램이 토큰을 받으면 "
                            "이 앱의 토큰이 무효가 됩니다(클라이언트당 토큰 1개). 다른 프로그램을 끄거나 키를 따로 발급받으세요")
        try:
            data = r.json()
        except ValueError:
            raise TossError(r.status_code, "invalid-response", r.text[:200])
        if r.status_code >= 400 or "error" in data:
            e = data.get("error") or {}
            raise TossError(r.status_code, e.get("code", "error"), e.get("message", ""), e.get("data"))
        return data.get("result")

    # 계좌
    def accounts(self) -> list[dict]:
        return self._call("GET", "/api/v1/accounts", "ACCOUNT") or []

    def account_seq(self) -> int:
        if self._account_seq is None:
            accs = [a for a in self.accounts() if a.get("accountType") == "BROKERAGE"]
            if not accs:
                raise TossError(404, "no-account", "종합매매 계좌가 없습니다")
            self._account_seq = int(accs[0]["accountSeq"])
        return self._account_seq

    def holdings(self) -> dict:
        return self._call("GET", "/api/v1/holdings", "ASSET", account=True) or {}

    def buying_power(self, currency: str) -> float:
        r = self._call("GET", "/api/v1/buying-power", "ORDER_INFO", {"currency": currency}, account=True)
        return num((r or {}).get("cashBuyingPower"))

    def account(self, market: Market) -> Account:
        h = self.holdings()
        pos = {}
        for it in h.get("items", []):
            if it.get("marketCountry") != market.value:
                continue
            q = num(it.get("quantity"))
            if q <= 0:
                continue
            pos[it["symbol"]] = Position(market, it["symbol"], int(q), num(it.get("averagePurchasePrice")),
                                         num(it.get("lastPrice")))
        cash = self.buying_power("KRW" if market == Market.KR else "USD")
        return Account(market, cash, pos)

    # 시세
    def prices(self, symbols: list[str]) -> dict[str, float]:
        out = {}
        syms = [toss_symbol(s) for s in symbols]
        for i in range(0, len(syms), 200):
            for x in self._call("GET", "/api/v1/prices", "MARKET_DATA", {"symbols": ",".join(syms[i:i + 200])}) or []:
                out[x["symbol"]] = num(x.get("lastPrice"))
        return out

    def price(self, market: Market, symbol: str) -> float:
        p = self.prices([symbol]).get(toss_symbol(symbol), 0.0)
        if p <= 0:
            raise TossError(404, "no-price", f"{symbol} 현재가 없음")
        return p

    def candles(self, symbol: str, interval: str = "1d", count: int = 200, indicator: bool = False) -> pd.DataFrame:
        """최근 count 개 봉을 오래된 순으로. 200개씩 nextBefore 로 과거를 넘겨 받는다."""
        rows: list[dict] = []
        before = None
        path = f"/api/v1/market-indicators/{symbol}/candles" if indicator else "/api/v1/candles"
        while len(rows) < count:
            params = {"interval": interval, "count": min(200, count - len(rows))}
            if not indicator:
                params["symbol"] = toss_symbol(symbol)
                params["adjusted"] = "true"
            if before:
                params["before"] = before  # requests 가 '+' 를 %2B 로 인코딩한다
            r = self._call("GET", path, "MARKET_INDICATOR" if indicator else "MARKET_DATA_CHART", params) or {}
            chunk = r.get("candles", [])
            rows += chunk
            before = r.get("nextBefore")
            if not chunk or not before:
                break
        if not rows:
            raise TossError(404, "no-candles", f"{symbol} 캔들 없음")
        df = pd.DataFrame([{
            "date": x["timestamp"], "open": num(x.get("openPrice")), "high": num(x.get("highPrice")),
            "low": num(x.get("lowPrice")), "close": num(x.get("closePrice")), "volume": num(x.get("volume"))}
            for x in rows])
        idx = pd.to_datetime(df.pop("date"), utc=True).dt.tz_convert(KST)
        df.index = idx.dt.tz_localize(None).dt.normalize() if interval == "1d" else idx.dt.tz_localize(None)
        df = df[~df.index.duplicated()].sort_index()
        return df[df["close"] > 0]

    def daily_bars(self, market: Market, symbol: str, days: int) -> pd.DataFrame:
        return self.candles(symbol, "1d", days)

    # 시장 정보
    def stocks(self, symbols: list[str]) -> list[dict]:
        out = []
        syms = [toss_symbol(s) for s in symbols]
        for i in range(0, len(syms), 200):
            out += self._call("GET", "/api/v1/stocks", "STOCK", {"symbols": ",".join(syms[i:i + 200])}) or []
        return out

    def stocks_all(self, market: str) -> list[dict]:
        """market: KOSPI | KOSDAQ | NYSE | NASDAQ | AMEX. 상장 중인 전 종목 (종목 검색용)."""
        return self._call("GET", "/api/v1/stocks/all", "STOCK", {"market": market}) or []

    def orderbook(self, symbol: str) -> dict:
        return self._call("GET", "/api/v1/orderbook", "MARKET_DATA", {"symbol": toss_symbol(symbol)}) or {}

    def trades(self, symbol: str, count: int = 30) -> list[dict]:
        return self._call("GET", "/api/v1/trades", "MARKET_DATA", {"symbol": toss_symbol(symbol), "count": count}) or []

    def warnings(self, symbol: str) -> list[dict]:
        return self._call("GET", f"/api/v1/stocks/{toss_symbol(symbol)}/warnings", "STOCK") or []

    def price_limits(self, symbol: str) -> dict:
        return self._call("GET", "/api/v1/price-limits", "MARKET_DATA", {"symbol": toss_symbol(symbol)}) or {}

    def sector_rankings(self, kind: str, market: str, duration: str = "1d") -> list[dict]:
        """kind: TOP_GAINERS | MARKET_TRADING_AMOUNT"""
        r = self._call("GET", "/api/v1/sectors/rankings", "SECTOR", {"type": kind, "marketCountry": market,
                                                                        "duration": duration}) or {}
        return r.get("rankings", [])

    def sector_stocks(self, sector_id: str, market: str) -> list[dict]:
        r = self._call("GET", f"/api/v1/sectors/{sector_id}/stocks", "SECTOR", {"marketCountry": market}) or {}
        return r.get("stocks", [])

    def sector_detail(self, sector_id: str, market: str) -> dict:
        return self._call("GET", f"/api/v1/sectors/{sector_id}", "SECTOR", {"marketCountry": market}) or {}

    def _trend(self, kind: str, symbol: str, count: int) -> dict:
        return self._call("GET", f"/api/v1/stocks/{toss_symbol(symbol)}/{kind}", "STOCK_TRADING_TREND",
                          {"count": count}) or {}

    def program_trades(self, symbol: str, count: int = 20) -> dict:
        return self._trend("program-trades", symbol, count)

    def short_selling(self, symbol: str, count: int = 20) -> dict:
        return self._trend("short-selling", symbol, count)

    def credit_trades(self, symbol: str, count: int = 20) -> dict:
        return self._trend("credit-trades", symbol, count)

    def commissions(self) -> list[dict]:
        return self._call("GET", "/api/v1/commissions", "ORDER_INFO", account=True) or []

    def sellable_quantity(self, symbol: str) -> float:
        r = self._call("GET", "/api/v1/sellable-quantity", "ORDER_INFO", {"symbol": toss_symbol(symbol)}, account=True)
        return num((r or {}).get("sellableQuantity"))

    def rankings(self, kind: str, market: str, duration: str = "1d", count: int = 30) -> dict:
        return self._call("GET", "/api/v1/rankings", "RANKING", {
            "type": kind, "marketCountry": market, "duration": duration, "count": count,
            "excludeInvestmentCaution": "false"}) or {}

    def indicator_prices(self, symbols: list[str] = INDICATORS) -> list[dict]:
        return self._call("GET", "/api/v1/market-indicators/prices", "MARKET_INDICATOR",
                          {"symbols": ",".join(symbols)}) or []

    def market_investor_trading(self, symbol: str = "KOSPI", count: int = 1) -> dict:
        return self._call("GET", f"/api/v1/market-indicators/{symbol}/investor-trading", "MARKET_INDICATOR",
                          {"interval": "1d", "count": count}) or {}

    def stock_investor_trading(self, symbol: str, count: int = 10) -> dict:
        return self._call("GET", f"/api/v1/stocks/{toss_symbol(symbol)}/investor-trading", "STOCK_TRADING_TREND",
                          {"count": count}) or {}

    def exchange_rate(self) -> dict:
        return self._call("GET", "/api/v1/exchange-rate", "MARKET_INFO",
                          {"baseCurrency": "USD", "quoteCurrency": "KRW"}) or {}

    def calendar(self, market: Market, date: str | None = None) -> dict:
        return self._call("GET", f"/api/v1/market-calendar/{market.value}", "MARKET_INFO",
                          {"date": date} if date else None) or {}

    def in_regular_session(self, market: Market, now: datetime | None = None) -> bool:
        """지금이 정규장 시간인지. 휴장일이면 세션이 null 이라 False.
        미국 정규장은 한국 날짜로 밤~새벽에 걸치므로 전 영업일 세션도 본다."""
        now = now or datetime.now(KST)
        cal = self.calendar(market)
        for day in ("today", "previousBusinessDay"):
            d = cal.get(day) or {}
            sess = (d.get("integrated") or {}).get("regularMarket") if market == Market.KR else d.get("regularMarket")
            if sess and datetime.fromisoformat(sess["startTime"]) <= now < datetime.fromisoformat(sess["endTime"]):
                return True
        return False

    # 주문
    def order(self, req: OrderRequest, client_order_id: str = "", loc: bool = False) -> OrderResult:
        body: dict = {"symbol": toss_symbol(req.symbol), "side": req.side.value.upper(), "quantity": str(req.qty)}
        if req.price <= 0:
            body["orderType"] = "MARKET"
        else:
            body["orderType"] = "LIMIT"
            body["price"] = str(int(req.price)) if req.market == Market.KR else f"{req.price:.2f}"
            if loc and req.market == Market.US:
                body["timeInForce"] = "CLS"  # LOC: 장 마감 지정가
        if client_order_id:
            body["clientOrderId"] = client_order_id[:36]
        if req.notional * (1 if req.market == Market.KR else 1400) >= 100_000_000:
            body["confirmHighValueOrder"] = True
        try:
            r = self._call("POST", "/api/v1/orders", "ORDER", body=body, account=True) or {}
            return OrderResult(True, r.get("orderId", ""), "주문 접수")
        except (TossError, requests.RequestException) as e:
            return OrderResult(False, message=str(e))

    def orders(self, status: str = "OPEN", symbol: str | None = None) -> list[dict]:
        p = {"status": status}
        if symbol:
            p["symbol"] = toss_symbol(symbol)
        return (self._call("GET", "/api/v1/orders", "ORDER_HISTORY", p, account=True) or {}).get("orders", [])

    def order_detail(self, order_id: str) -> dict:
        return self._call("GET", f"/api/v1/orders/{order_id}", "ORDER_HISTORY", account=True) or {}

    def cancel(self, order_id: str) -> dict:
        return self._call("POST", f"/api/v1/orders/{order_id}/cancel", "ORDER", body={}, account=True) or {}
