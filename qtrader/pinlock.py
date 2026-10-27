"""실전 주문 PIN. 실전 모드로 바꾸기, 자동매매 켜기, 주문 승인·즉시 실행·취소 전에 확인한다.

PIN 은 저장하지 않고 PBKDF2-SHA256 해시만 둔다. 5번 틀리면 5분 잠근다. 맞으면 5분 동안 다시 묻지 않는다.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import time
from pathlib import Path

ITER = 200_000
MAX_FAIL, LOCK_SEC, UNLOCK_SEC = 5, 300, 300

_lock = threading.Lock()
_state = {"fails": 0, "locked_until": 0.0, "unlocked_until": 0.0}


class PinError(Exception):
    """code: need_set | need_pin | wrong | locked | bad"""

    def __init__(self, code: str, msg: str):
        super().__init__(msg)
        self.code = code


def _file(home: Path) -> Path:
    return home / "pin.json"


def _hash(pin: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, ITER).hex()


def is_set(home: Path) -> bool:
    return _file(home).exists()


def _check_format(pin: str) -> None:
    if not (pin.isdigit() and 4 <= len(pin) <= 8):
        raise PinError("bad", "PIN 은 숫자 4~8자리입니다")
    if len(set(pin)) == 1 or pin in "0123456789" or pin in "9876543210":
        raise PinError("bad", "같은 숫자나 연속된 숫자는 쓸 수 없습니다")


def _match(home: Path, pin: str) -> bool:
    d = json.loads(_file(home).read_text(encoding="utf-8"))
    return hmac.compare_digest(_hash(pin, bytes.fromhex(d["salt"])), d["hash"])


def set_pin(home: Path, new: str, old: str = "") -> None:
    _check_format(new)
    if is_set(home):
        verify(home, old)
    salt = os.urandom(16)
    _file(home).write_text(json.dumps({"salt": salt.hex(), "hash": _hash(new, salt), "iter": ITER}), encoding="utf-8")
    with _lock:
        _state["unlocked_until"] = 0.0


def verify(home: Path, pin: str | None) -> None:
    """맞으면 통과, 아니면 PinError. 최근에 맞혔으면 PIN 없이도 통과."""
    if not is_set(home):
        raise PinError("need_set", "실전 주문 PIN 을 먼저 정하세요 (설정 → 실전 주문 PIN)")
    now = time.time()
    with _lock:
        if _state["locked_until"] > now:
            raise PinError("locked", f"PIN 을 여러 번 틀려 {int(_state['locked_until'] - now) // 60 + 1}분 동안 잠겼습니다")
        if not pin:
            if _state["unlocked_until"] > now:
                return
            raise PinError("need_pin", "실전 주문 PIN 을 입력하세요")
        if _match(home, pin):
            _state.update(fails=0, unlocked_until=now + UNLOCK_SEC)
            return
        _state["fails"] += 1
        left = MAX_FAIL - _state["fails"]
        if left <= 0:
            _state.update(fails=0, locked_until=now + LOCK_SEC)
            raise PinError("locked", "PIN 을 5번 틀려 5분 동안 잠겼습니다")
        raise PinError("wrong", f"PIN 이 틀렸습니다 ({left}번 남음)")


def lock_now() -> None:
    with _lock:
        _state["unlocked_until"] = 0.0


def reset_state() -> None:
    with _lock:
        _state.update(fails=0, locked_until=0.0, unlocked_until=0.0)
