"""뉴스: RSS 피드를 읽어 제목·시간·링크만 보여준다 (RSS 리더와 같은 방식, 본문은 원문 사이트에서 본다).

기본 피드는 인베스팅닷컴 한국어판이 RSS 리더용으로 공개한 '주식 시장 뉴스'
(https://kr.investing.com/webmaster-tools/rss). 피드 데이터의 저작권은 각 제공자에게 있으므로
본문을 저장·재배포하지 않고 제목과 원문 링크만 쓴다. 종목 뉴스는 구글 뉴스 RSS 검색을 쓴다.
"""
from __future__ import annotations

import html
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


def parse(xml: bytes, source: str) -> list[dict]:
    root = ET.fromstring(xml)
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
    r = (session or requests).get(url, headers=UA, timeout=8)
    r.raise_for_status()
    items = parse(r.content, source)
    with _lock:
        _cache[url] = (time.monotonic(), items)
    return items


def feeds(cfg_feeds: list[dict] | None = None, limit: int = 60, session=None) -> dict:
    items, errors = [], []
    for f in cfg_feeds or DEFAULT_FEEDS:
        try:
            items += fetch(f["url"], f["name"], session=session)
        except Exception as e:  # 한 피드 실패가 전체를 막지 않게
            errors.append(f"{f['name']}: {e}")
    seen, uniq = set(), []
    for x in sorted(items, key=lambda x: x["time"] or "", reverse=True):
        if x["link"] not in seen:
            seen.add(x["link"])
            uniq.append(x)
    return {"items": uniq[:limit], "errors": errors}


def stock_news(query: str, limit: int = 20, session=None) -> list[dict]:
    """구글 뉴스 RSS 검색 (한국어). query 는 종목명."""
    url = f"https://news.google.com/rss/search?q={quote(query)}&hl=ko&gl=KR&ceid=KR:ko"
    return fetch(url, "구글 뉴스", ttl=600, session=session)[:limit]
