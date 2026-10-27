"""기록 문장을 사람이 읽는 말로. 저장된 기록은 그대로 두고 보여 줄 때만 바꾼다."""
from __future__ import annotations

import re

SIDE = {"buy": "매수", "sell": "매도"}
CODE = re.compile(r"(?<![\w.])(\d{6}|(?:NYSE|AMEX|NASDAQ):[A-Z.]+|[A-Z]{2,5})(?![\w])")


def names(text: str, name_of) -> str:
    """종목 코드를 이름으로 (이름을 모르면 그대로)."""
    def rep(m):
        s = m.group(1)
        n = name_of(s)
        return n if n and n != s else s
    return CODE.sub(rep, text)


def say(msg: str, name_of=lambda s: s) -> str:
    m = re.match(r"^(dry_run|filled|submitted|rejected|pending_approval) (buy|sell) (\S+) x(\d+) @ ([\d,.]+) ?(.*)$", msg)
    if m:
        st, side, sym, qty, px, rest = m.groups()
        who = f"{name_of(sym)} {qty}주 {SIDE[side]}"
        return {"dry_run": f"{who} — 미리보기라 계산만 했어요 (주문 안 함)",
                "filled": f"{who} 체결됐어요 ({px})",
                "submitted": f"{who} 주문을 넣었어요 ({px}) · 체결되면 알려 드려요",
                "rejected": f"{who} 주문이 거부됐어요: {rest}",
                "pending_approval": f"{who} 승인을 기다려요"}[st]
    m = re.match(r"^실행 완료: 주문 (\d+)건, 차단 (\d+)건(.*)$", msg)
    if m:
        n, b, rest = m.groups()
        head = "미리보기 계산 끝" if "dry" in rest else "오늘 실행 끝"
        return f"{head}: 주문 {n}건" + (f", 규칙에 막힌 주문 {b}건" if b != "0" else "")
    m = re.match(r"^직접 (매수|매도) (\S+) x(\d+) -> (\w+) ?(.*)$", msg)
    if m:
        side, sym, qty, st, rest = m.groups()
        return f"직접 {side}한 {name_of(sym)} {qty}주 " + ({"filled": "체결됐어요", "submitted": "주문을 넣었어요"}.get(st, f"거부됐어요: {rest}"))
    m = re.match(r"^(\S+) (buy|sell) (체결|취소됨|거부됨|기간 만료|부분 체결)(.*)$", msg)
    if m:
        sym, side, st, rest = m.groups()
        return f"{name_of(sym)} {SIDE[side]} {st}{rest}"
    msg = re.sub(r"킬 스위치", "긴급 정지", msg)
    msg = msg.replace("dry-run", "미리보기").replace("dry_run", "미리보기")
    return names(msg, name_of)
