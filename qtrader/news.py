"""뉴스: RSS 피드를 읽어 제목·시간·링크만 보여준다 (RSS 리더와 같은 방식, 본문은 원문 사이트에서 본다).

기본 피드는 인베스팅닷컴 한국어판이 RSS 리더용으로 공개한 '주식 시장 뉴스'
(https://kr.investing.com/webmaster-tools/rss). 피드 데이터의 저작권은 각 제공자에게 있으므로
본문을 저장·재배포하지 않고 제목과 원문 링크만 쓴다. 종목 뉴스는 구글 뉴스 RSS 검색을 쓴다.
"""
from __future__ import annotations

import html
import os
import re
import threading
import time
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import requests
from defusedxml import ElementTree as ET

DEFAULT_FEEDS = [
    {"name": "인베스팅닷컴 · 주식시장", "url": "https://kr.investing.com/rss/news_25.rss"},
    {"name": "인베스팅닷컴 · 전체", "url": "https://kr.investing.com/rss/news.rss"},
]
UA = {"User-Agent": "Mozilla/5.0 (QTrader RSS reader)"}
_cache: dict[str, tuple[float, list]] = {}
_lock = threading.Lock()
TAG = re.compile(r"<[^>]+>")


def _text(el, name: str) -> str:
    x = el.find(name)
    return (x.text or "").strip() if x is not None and x.text else ""


def to_utf8(xml: bytes) -> bytes:
    """EUC-KR(CP949) 로 된 한국 언론사 피드는 XML 파서가 못 읽으므로 UTF-8 로 바꾼다."""
    head = xml[:200].lower()
    m = re.search(rb"encoding=[\"']([a-z0-9_-]+)[\"']", head)
    enc = m.group(1).decode() if m else "utf-8"
    if enc in ("utf-8", "utf8"):
        return xml
    text = xml.decode("cp949" if enc in ("euc-kr", "euckr", "ks_c_5601-1987", "cp949") else enc, errors="replace")
    return re.sub(r"encoding=[\"'][^\"']+[\"']", 'encoding="utf-8"', text, count=1).encode("utf-8")


def parse(xml: bytes, source: str) -> list[dict]:
    root = ET.fromstring(to_utf8(xml))
    out = []
    for it in root.iter("item"):
        title = html.unescape(_text(it, "title"))
        link = _text(it, "link")
        if not title or not link.startswith(("http://", "https://")):
            continue
        ts = None
        pub = _text(it, "pubDate")
        if pub:
            try:
                ts = parsedate_to_datetime(pub).isoformat()
            except (TypeError, ValueError):
                ts = None
        desc = TAG.sub("", html.unescape(_text(it, "description")))[:160]
        src = _text(it, "source") or source
        out.append({"title": title, "link": link, "time": ts, "summary": desc, "source": src})
    return out


def fetch(url: str, source: str, ttl: float = 300, session=None) -> list[dict]:
    with _lock:
        hit = _cache.get(url)
        if hit and time.monotonic() - hit[0] < ttl:
            return hit[1]
    if session is None and os.environ.get("QTRADER_OFFLINE"):  # 시험 중에는 밖으로 나가지 않는다 (가짜 세션은 통과)
        return []
    r = (session or requests).get(url, headers=UA, timeout=8)
    r.raise_for_status()
    items = parse(r.content, source)
    with _lock:
        _cache[url] = (time.monotonic(), items)
    return items


# 해외증시 기사로 보는 낱말 (국제 섹션처럼 정치 기사가 섞인 피드를 거르고, 국내 피드의 미국 증시 기사를 옮긴다)
GLOBAL_WORDS = re.compile(r"뉴욕증시|뉴욕 증시|미국 ?증시|미국 ?주식|미장|나스닥|다우|S&P|월가|월스트리트|연준|파월|FOMC|미 국채|"
                          r"서학개미|해외주식|빅테크|엔비디아|테슬라|애플|마이크로소프트|아마존|알파벳|구글|메타|브로드컴|TSMC|"
                          r"팔란티어|앤트로픽|오픈AI|ETF|환율|국제유가|금값|중국증시|닛케이|유럽증시|증시|주가|株")


def _tidy(x: dict) -> dict:
    """구글 뉴스: 제목 끝 '- 언론사' 를 떼어 출처로, 제목만 되풀이한 요약은 지운다."""
    title, src = x["title"], x.get("source") or ""
    m = re.match(r"^(.*\S)\s+-\s+([^-]{1,40})$", title)
    if "news.google." in (x.get("link") or "") and m:
        title, src = m.group(1), m.group(2).strip()
    if src.startswith("http"):
        src = re.sub(r"^https?://(www\.)?|/.*$", "", src)
    summ = x.get("summary") or ""
    if summ.replace(" ", "").startswith(title.replace(" ", "")[:20]):
        summ = ""
    return x | {"title": title, "source": src or x.get("source"), "summary": summ}


def feeds(cfg_feeds: list[dict] | None = None, limit: int = 300, session=None) -> dict:
    """limit 은 국내·해외 각각의 최대 건수."""
    from concurrent.futures import ThreadPoolExecutor

    items, errors = [], []
    flist = cfg_feeds or DEFAULT_FEEDS

    def one(f):
        return f, fetch(f["url"], f["name"], session=session)

    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(one, f) for f in flist]
        for fu, f in zip(futs, flist):
            try:
                _, got = fu.result()
            except Exception as e:  # 한 피드 실패가 전체를 막지 않게
                errors.append(f"{f['name']}: {e}")
                continue
            for x in got:
                text = x["title"]
                cat = f.get("cat", "kr")
                if f.get("filter") and not GLOBAL_WORDS.search(text):
                    continue  # 국제 섹션의 증시와 무관한 기사
                if cat == "kr" and re.search(r"뉴욕증시|뉴욕 증시|미국 ?증시|나스닥|월가|서학개미", x["title"]):
                    cat = "global"
                items.append(_tidy(x) | {"cat": cat, "feed": f["name"]})
    seen, uniq, count = set(), [], {"kr": 0, "global": 0}
    for x in sorted(items, key=lambda x: x["time"] or "", reverse=True):
        key = re.sub(r"\s+-\s+[^-]+$", "", x["title"]).replace(" ", "")[:40]  # 구글 뉴스의 '- 언론사' 꼬리 무시
        if x["link"] in seen or key in seen or count[x["cat"]] >= limit:
            continue
        seen.update((x["link"], key))
        count[x["cat"]] += 1
        uniq.append(x)
    return {"items": uniq, "errors": errors}


def stock_news(query: str, limit: int = 20, session=None) -> list[dict]:
    """구글 뉴스 RSS 검색 (한국어). query 는 종목명."""
    url = f"https://news.google.com/rss/search?q={quote(query)}&hl=ko&gl=KR&ceid=KR:ko"
    return fetch(url, "구글 뉴스", ttl=600, session=session)[:limit]
