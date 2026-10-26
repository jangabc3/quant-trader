"""한국투자증권 Open API 클라이언트 (국내·미국 주식).

엔드포인트와 TR ID 는 공식 예제 저장소 koreainvestment/open-trading-api 기준.
실전: https://openapi.koreainvestment.com:9443
모의: https://openapivts.koreainvestment.com:29443
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

from .models import Account, Market, OrderRequest, OrderResult, Position, Side

URLS = {"real": "https://openapi.koreainvestment.com:9443",
        "demo": "https://openapivts.koreainvestment.com:29443"}

# 모의투자는 초당 호출 한도가 낮다
MIN_INTERVAL = {"real": 0.07, "demo": 0.55}

# 미국 거래소 코드: 주문/잔고용 -> 시세용
US_PRICE_EXCD = {"NASD": "NAS", "NYSE": "NYS", "AMEX": "AMS"}


class KISError(RuntimeError):
    pass


def split_us(symbol: str) -> tuple[str, str]:
    """'NYSE:KO' -> ('NYSE', 'KO'), 'AAPL' -> ('NASD', 'AAPL')"""
    if ":" in symbol:
        ex, sym = symbol.split(":", 1)
        ex = ex.upper()
        if ex not in US_PRICE_EXCD:
            raise ValueError(f"미국 거래소는 NASD/NYSE/AMEX 중 하나: {symbol}")
        return ex, sym.upper()
    return "NASD", symbol.upper()


class KIS:
    def __init__(self, env: str, app_key: str, app_secret: str, account: str, token_dir: Path,
                 session: requests.Session | None = None):
        if env not in URLS:
            raise ValueError("env 는 real 또는 demo")
        cano, _, prdt = account.partition("-")
        if not (cano.isdigit() and len(cano) == 8 and prdt.isdigit() and len(prdt) == 2):
            raise ValueError("계좌번호는 12345678-01 형식")
        self.env, self.key, self.secret = env, app_key, app_secret
        self.cano, self.prdt = cano, prdt
        self.base = URLS[env]
        self.http = session or requests.Session()
        self.token_file = Path(token_dir) / f"kis_token_{env}_{app_key[-6:]}.json"
        self._token: str | None = None
        self._expires = datetime.min
        self._last = 0.0
        self._lock = threading.Lock()

    # 인증 — 토큰은 하루 발급 횟수 제한이 있어 파일에 캐시한다
    def token(self) -> str:
        if self._token and datetime.now() < self._expires:
            return self._token
        if self.token_file.exists():
            d = json.loads(self.token_file.read_text())
            exp = datetime.fromisoformat(d["expires"])
            if datetime.now() < exp:
                self._token, self._expires = d["token"], exp
                return self._token
        r = self.http.post(f"{self.base}/oauth2/tokenP", timeout=10, json={
            "grant_type": "client_credentials", "appkey": self.key, "appsecret": self.secret})
        body = r.json()
        if "access_token" not in body:
            raise KISError(f"토큰 발급 실패: {body.get('error_description') or body}")
        self._token = body["access_token"]
        # 만료 10분 전에 새로 받는다
        self._expires = datetime.now() + timedelta(seconds=int(body.get("expires_in", 86400)) - 600)
        self.token_file.write_text(json.dumps({"token": self._token, "expires": self._expires.isoformat()}))
        return self._token

    def _call(self, method: str, path: str, tr_id: str, params: dict, tr_cont: str = "") -> tuple[dict, dict]:
        with self._lock:
            wait = MIN_INTERVAL[self.env] - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.token()}",
            "appkey": self.key, "appsecret": self.secret,
            "tr_id": tr_id, "custtype": "P", "tr_cont": tr_cont,
        }
        url = self.base + path
        if method == "GET":
            r = self.http.get(url, headers=headers, params=params, timeout=10)
        else:
            r = self.http.post(url, headers=headers, data=json.dumps(params), timeout=10)
        try:
            body = r.json()
        except ValueError:
            raise KISError(f"{tr_id} HTTP {r.status_code}: {r.text[:200]}")
        if body.get("rt_cd") != "0":
            raise KISError(f"{tr_id} {body.get('msg_cd', '')} {body.get('msg1', r.status_code)}".strip())
        return body, dict(r.headers)

    def _tr(self, real_id: str) -> str:
        return real_id if self.env == "real" else "V" + real_id[1:]

    # 시세
    def price(self, market: Market, symbol: str) -> float:
        if market == Market.KR:
            b, _ = self._call("GET", "/uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100",
                              {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol})
            return float(b["output"]["stck_prpr"])
        ex, sym = split_us(symbol)
        b, _ = self._call("GET", "/uapi/overseas-price/v1/quotations/price", "HHDFS00000300",
                          {"AUTH": "", "EXCD": US_PRICE_EXCD[ex], "SYMB": sym})
        last = b["output"].get("last") or "0"
        if float(last) <= 0:
            raise KISError(f"{symbol} 현재가를 받지 못했습니다")
        return float(last)

    def daily_bars(self, market: Market, symbol: str, days: int) -> pd.DataFrame:
        """최근 days 거래일 일봉 (수정주가). 한 번에 100개씩 과거로 넘겨 받는다."""
        rows: list[dict] = []
        if market == Market.KR:
            end = datetime.now()
            while len(rows) < days:
                start = end - timedelta(days=140)
                b, _ = self._call("GET", "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
                                  "FHKST03010100", {
                                      "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol,
                                      "FID_INPUT_DATE_1": start.strftime("%Y%m%d"),
                                      "FID_INPUT_DATE_2": end.strftime("%Y%m%d"),
                                      "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"})
                chunk = [x for x in b.get("output2", []) if x.get("stck_bsop_date")]
                if not chunk:
                    break
                rows += [{"date": x["stck_bsop_date"], "open": x["stck_oprc"], "high": x["stck_hgpr"],
                          "low": x["stck_lwpr"], "close": x["stck_clpr"], "volume": x["acml_vol"]} for x in chunk]
                end = datetime.strptime(min(x["stck_bsop_date"] for x in chunk), "%Y%m%d") - timedelta(days=1)
        else:
            ex, sym = split_us(symbol)
            bymd = ""
            while len(rows) < days:
                b, _ = self._call("GET", "/uapi/overseas-price/v1/quotations/dailyprice", "HHDFS76240000", {
                    "AUTH": "", "EXCD": US_PRICE_EXCD[ex], "SYMB": sym, "GUBN": "0", "BYMD": bymd, "MODP": "1"})
                chunk = [x for x in b.get("output2", []) if x.get("xymd")]
                if not chunk:
                    break
                rows += [{"date": x["xymd"], "open": x["open"], "high": x["high"], "low": x["low"],
                          "close": x["clos"], "volume": x["tvol"]} for x in chunk]
                oldest = datetime.strptime(min(x["xymd"] for x in chunk), "%Y%m%d")
                bymd = (oldest - timedelta(days=1)).strftime("%Y%m%d")
        if not rows:
            raise KISError(f"{symbol} 일봉이 없습니다")
        df = pd.DataFrame(rows).drop_duplicates("date")
        df.index = pd.to_datetime(df.pop("date"), format="%Y%m%d")
        df = df.astype(float).sort_index()
        return df[df["close"] > 0].tail(days)

    # 계좌
    def account(self, market: Market) -> Account:
        if market == Market.KR:
            b, _ = self._call("GET", "/uapi/domestic-stock/v1/trading/inquire-balance", self._tr("TTTC8434R"), {
                "CANO": self.cano, "ACNT_PRDT_CD": self.prdt, "AFHR_FLPR_YN": "N", "OFL_YN": "",
                "INQR_DVSN": "02", "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N",
                "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "00", "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""})
            pos = {}
            for x in b.get("output1", []):
                q = int(float(x.get("hldg_qty", 0)))
                if q > 0:
                    pos[x["pdno"]] = Position(Market.KR, x["pdno"], q, float(x["pchs_avg_pric"]), float(x["prpr"]))
            summ = (b.get("output2") or [{}])[0]
            # D+2 예수금이 실제 매수 가능 금액에 가깝다
            cash = float(summ.get("prvs_rcdl_excc_amt") or summ.get("dnca_tot_amt") or 0)
            return Account(Market.KR, cash, pos)

        pos = {}
        for ex in US_PRICE_EXCD:
            b, _ = self._call("GET", "/uapi/overseas-stock/v1/trading/inquire-balance", self._tr("TTTS3012R"), {
                "CANO": self.cano, "ACNT_PRDT_CD": self.prdt, "OVRS_EXCG_CD": ex, "TR_CRCY_CD": "USD",
                "CTX_AREA_FK200": "", "CTX_AREA_NK200": ""})
            for x in b.get("output1", []):
                q = int(float(x.get("ovrs_cblc_qty", 0)))
                if q > 0:
                    sym = x["ovrs_pdno"] if ex == "NASD" else f"{ex}:{x['ovrs_pdno']}"
                    pos[sym] = Position(Market.US, sym, q, float(x["pchs_avg_pric"]), float(x["now_pric2"]))
        b, _ = self._call("GET", "/uapi/overseas-stock/v1/trading/inquire-psamount", self._tr("TTTS3007R"), {
            "CANO": self.cano, "ACNT_PRDT_CD": self.prdt, "OVRS_EXCG_CD": "NASD",
            "OVRS_ORD_UNPR": "1", "ITEM_CD": "AAPL"})
        cash = float(b["output"].get("ovrs_ord_psbl_amt") or 0)
        return Account(Market.US, cash, pos)

    # 주문
    def order(self, req: OrderRequest) -> OrderResult:
        try:
            if req.market == Market.KR:
                tr = self._tr("TTTC0012U" if req.side == Side.BUY else "TTTC0011U")
                market_order = req.price <= 0
                b, _ = self._call("POST", "/uapi/domestic-stock/v1/trading/order-cash", tr, {
                    "CANO": self.cano, "ACNT_PRDT_CD": self.prdt, "PDNO": req.symbol,
                    "ORD_DVSN": "01" if market_order else "00", "ORD_QTY": str(req.qty),
                    "ORD_UNPR": "0" if market_order else str(int(req.price)),
                    "EXCG_ID_DVSN_CD": "KRX", "SLL_TYPE": "01" if req.side == Side.SELL else "", "CNDT_PRIC": ""})
            else:
                ex, sym = split_us(req.symbol)
                real_tr = "TTTT1002U" if req.side == Side.BUY else "TTTT1006U"
                tr = real_tr if self.env == "real" else ("VTTT1002U" if req.side == Side.BUY else "VTTT1001U")
                b, _ = self._call("POST", "/uapi/overseas-stock/v1/trading/order", tr, {
                    "CANO": self.cano, "ACNT_PRDT_CD": self.prdt, "OVRS_EXCG_CD": ex, "PDNO": sym,
                    "ORD_QTY": str(req.qty), "OVRS_ORD_UNPR": f"{req.price:.2f}", "CTAC_TLNO": "",
                    "MGCO_APTM_ODNO": "", "SLL_TYPE": "00" if req.side == Side.SELL else "",
                    "ORD_SVR_DVSN_CD": "0", "ORD_DVSN": "00"})
            out = b.get("output", {})
            return OrderResult(True, out.get("ODNO", ""), b.get("msg1", "").strip())
        except (KISError, requests.RequestException) as e:
            return OrderResult(False, message=str(e))
