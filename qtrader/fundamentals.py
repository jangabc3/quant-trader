"""재무제표: 국내는 DART 오픈API(무료 인증키), 미국은 SEC EDGAR XBRL companyfacts(무료, 연락처 필요).

- DART: https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json (crtfc_key, corp_code, bsns_year, reprt_code, fs_div)
  사업보고서(11011) 한 번에 당기·전기·전전기 3개 연도가 온다. 종목코드 -> 고유번호는 corpCode.xml(zip).
- SEC: https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json, 티커 -> CIK 는 company_tickers.json.
  SEC 는 User-Agent 에 연락처(이메일)를 요구한다. 설정에 사용자가 넣은 값을 그대로 보낸다.
"""
from __future__ import annotations

import io
import json
import threading
import time
import zipfile
from datetime import date
from pathlib import Path

import requests
from defusedxml import ElementTree as ET

DART = "https://opendart.fss.or.kr/api"
_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()

# 사업보고서 계정 이름 (IFRS 회사마다 조금씩 달라 후보를 둔다)
KR_ACCOUNTS = {
    "revenue": ["매출액", "수익(매출액)", "영업수익", "매출", "수익"],
    "operating_income": ["영업이익", "영업이익(손실)"],
    "net_income": ["당기순이익", "당기순이익(손실)", "연결당기순이익", "지배기업의 소유주에게 귀속되는 당기순이익"],
    "assets": ["자산총계"], "liabilities": ["부채총계"], "equity": ["자본총계"],
    "operating_cf": ["영업활동현금흐름", "영업활동으로 인한 현금흐름", "영업활동 현금흐름"],
}
US_TAGS = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"],
    "operating_income": ["OperatingIncomeLoss"], "net_income": ["NetIncomeLoss"],
    "assets": ["Assets"], "liabilities": ["Liabilities"], "equity": ["StockholdersEquity"],
    "operating_cf": ["NetCashProvidedByUsedInOperatingActivities"],
    "eps": ["EarningsPerShareDiluted", "EarningsPerShareBasic"],
}


class FundamentalsError(RuntimeError):
    pass


def _cached(key: str, ttl: float, fn):
    with _lock:
        hit = _cache.get(key)
        if hit and time.monotonic() - hit[0] < ttl:
            return hit[1]
    v = fn()
    with _lock:
        _cache[key] = (time.monotonic(), v)
    return v


def _num(s) -> float | None:
    try:
        return float(str(s).replace(",", "")) if s not in (None, "", "-") else None
    except ValueError:
        return None


# DART
def dart_corp_codes(api_key: str, home: Path, session=None) -> dict[str, str]:
    """종목코드 -> DART 고유번호. 하루 한 번 내려받아 파일로 둔다."""
    f = Path(home) / "dart_corp_codes.json"
    if f.exists() and time.time() - f.stat().st_mtime < 86400:
        return json.loads(f.read_text(encoding="utf-8"))
    r = (session or requests).get(f"{DART}/corpCode.xml", params={"crtfc_key": api_key}, timeout=30)
    r.raise_for_status()
    try:
        z = zipfile.ZipFile(io.BytesIO(r.content))
    except zipfile.BadZipFile:
        raise FundamentalsError("DART 고유번호 파일을 받지 못했습니다. 인증키를 확인하세요")
    root = ET.fromstring(z.read(z.namelist()[0]))
    out = {}
    for it in root.iter("list"):
        sc = (it.findtext("stock_code") or "").strip()
        if sc:
            out[sc] = it.findtext("corp_code").strip()
    f.write_text(json.dumps(out), encoding="utf-8")
    return out


def _pick(rows: list[dict], names: list[str], sj: tuple[str, ...]) -> dict | None:
    for n in names:
        for r in rows:
            if r.get("sj_div") in sj and r.get("account_nm", "").replace(" ", "") == n.replace(" ", ""):
                return r
    return None


def dart_financials(symbol: str, api_key: str, home: Path, session=None) -> dict:
    def build():
        codes = dart_corp_codes(api_key, home, session)
        corp = codes.get(symbol)
        if not corp:
            raise FundamentalsError(f"{symbol} 의 DART 고유번호가 없습니다")
        rows, year_used = [], None
        for y in (date.today().year - 1, date.today().year - 2):
            for fs in ("CFS", "OFS"):
                r = (session or requests).get(f"{DART}/fnlttSinglAcntAll.json", timeout=15, params={
                    "crtfc_key": api_key, "corp_code": corp, "bsns_year": str(y), "reprt_code": "11011", "fs_div": fs})
                d = r.json()
                if d.get("status") == "000" and d.get("list"):
                    rows, year_used = d["list"], y
                    break
                if d.get("status") not in ("000", "013"):  # 013 = 조회된 데이터 없음
                    raise FundamentalsError(f"DART {d.get('status')}: {d.get('message')}")
            if rows:
                break
        if not rows:
            raise FundamentalsError("DART 사업보고서 재무제표를 찾지 못했습니다")
        years = [year_used - 2, year_used - 1, year_used]
        series = {}
        for k, names in KR_ACCOUNTS.items():
            sj = ("CF",) if k == "operating_cf" else (("BS",) if k in ("assets", "liabilities", "equity") else ("IS", "CIS"))
            r = _pick(rows, names, sj)
            series[k] = [_num(r.get("bfefrmtrm_amount")), _num(r.get("frmtrm_amount")), _num(r.get("thstrm_amount"))] if r else [None] * 3
        return {"source": "DART 사업보고서", "currency": "KRW", "years": years, "series": series,
                "source_url": f"https://dart.fss.or.kr/dsab007/main.do?autoSearch=true&textCrpCik={corp}"}

    return _cached(f"dart:{symbol}", 6 * 3600, build)


# SEC
def sec_financials(ticker: str, contact: str, session=None) -> dict:
    if not contact or "@" not in contact:
        raise FundamentalsError("SEC 자료를 받으려면 설정에 연락처 이메일을 넣어야 합니다 (SEC 접근 규칙)")
    ua = {"User-Agent": f"QTrader personal research {contact}"}
    http = session or requests

    def tickers():
        r = http.get("https://www.sec.gov/files/company_tickers.json", headers=ua, timeout=20)
        r.raise_for_status()
        return {v["ticker"].upper(): int(v["cik_str"]) for v in r.json().values()}

    def build():
        cik = _cached("sec:tickers", 86400, tickers).get(ticker.upper())
        if not cik:
            raise FundamentalsError(f"{ticker} 의 SEC CIK 를 찾지 못했습니다")
        r = http.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json", headers=ua, timeout=20)
        r.raise_for_status()
        facts = r.json().get("facts", {}).get("us-gaap", {})
        series, years = {}, set()
        annual: dict[str, dict[int, float]] = {}
        for k, tags in US_TAGS.items():
            for tag in tags:
                units = facts.get(tag, {}).get("units", {})
                vals = units.get("USD/shares" if k == "eps" else "USD", [])
                got = {}
                for v in vals:
                    if v.get("form") == "10-K" and v.get("fp") == "FY" and v.get("fy"):
                        got[int(v["fy"])] = float(v["val"])  # 같은 연도는 마지막(최신 정정) 값
                if got:
                    annual[k] = got
                    years |= set(got)
                    break
        ys = sorted(years)[-4:]
        for k in US_TAGS:
            series[k] = [annual.get(k, {}).get(y) for y in ys]
        return {"source": "SEC 10-K (XBRL)", "currency": "USD", "years": ys, "series": series,
                "source_url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=10-K"}

    return _cached(f"sec:{ticker}", 6 * 3600, build)


def ratios(fin: dict, price: float | None = None, shares: float | None = None) -> dict:
    """최근 연도 기준 마진·ROE·부채비율·PER·PBR. 계산할 수 없으면 None."""
    s = fin["series"]
    last = lambda k: (s.get(k) or [None])[-1]
    rev, op, ni, eq, li = last("revenue"), last("operating_income"), last("net_income"), last("equity"), last("liabilities")
    out = {
        "operating_margin": op / rev if rev and op is not None else None,
        "net_margin": ni / rev if rev and ni is not None else None,
        "roe": ni / eq if eq and ni is not None else None,
        "debt_ratio": li / eq if eq and li is not None else None,
        "revenue_growth": None, "per": None, "pbr": None, "market_cap": None,
    }
    r = s.get("revenue") or []
    if len(r) >= 2 and r[-1] and r[-2]:
        out["revenue_growth"] = r[-1] / r[-2] - 1
    eps = last("eps")
    if price and eps:
        out["per"] = price / eps if eps > 0 else None
    if price and shares:
        cap = price * shares
        out["market_cap"] = cap
        if ni and ni > 0 and out["per"] is None:
            out["per"] = cap / ni
        if eq and eq > 0:
            out["pbr"] = cap / eq
    return out


def for_symbol(t, symbol: str) -> dict:
    """한 종목의 재무제표 + 밸류에이션(PER·PBR·ROE 등). 국내 DART, 미국 SEC. 실패하면 {"error": ...}."""
    from . import config as config_mod, stockdata, vault
    s = symbol.upper().split(":")[-1]
    try:
        if s[:1].isdigit():
            key = vault.get("dart_api_key")
            if not key:
                raise FundamentalsError("국내 재무제표는 설정에서 DART 오픈API 인증키(무료)를 등록하면 볼 수 있습니다")
            fin = dart_financials(s, key, config_mod.home())
        else:
            fin = sec_financials(s, config_mod.load().get("sec_contact", ""))
    except FundamentalsError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"재무제표를 받지 못했습니다: {e}"}
    sm = stockdata.summary(t, symbol)
    return fin | {"ratios": ratios(fin, sm["price"], sm["shares"])}
