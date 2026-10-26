"""API 키 보관. 윈도우에서는 자격 증명 관리자, 맥은 키체인에 들어간다. 파일에는 남기지 않는다.

이름 규칙
- KIS:  kis_{demo|real}_app_key / kis_{demo|real}_app_secret
- 토스증권: toss_client_id / toss_client_secret
- Claude: anthropic_api_key
"""
from __future__ import annotations

import keyring
import keyring.errors

SERVICE = "QTrader"


def put(name: str, value: str) -> None:
    keyring.set_password(SERVICE, name, value.strip())


def get(name: str) -> str | None:
    try:
        return keyring.get_password(SERVICE, name)
    except keyring.errors.KeyringError:
        return None


def delete(name: str) -> None:
    try:
        keyring.delete_password(SERVICE, name)
    except keyring.errors.PasswordDeleteError:
        pass


def _pair(prefix: str, a: str, b: str) -> tuple[str, str] | None:
    x, y = get(f"{prefix}_{a}"), get(f"{prefix}_{b}")
    return (x, y) if x and y else None


# KIS
def set_kis(env: str, app_key: str, app_secret: str) -> None:
    put(f"kis_{env}_app_key", app_key)
    put(f"kis_{env}_app_secret", app_secret)


def get_kis(env: str) -> tuple[str, str] | None:
    return _pair(f"kis_{env}", "app_key", "app_secret")


def has_kis(env: str) -> bool:
    return get_kis(env) is not None


def delete_kis(env: str) -> None:
    delete(f"kis_{env}_app_key")
    delete(f"kis_{env}_app_secret")


# 토스증권
def set_toss(client_id: str, client_secret: str) -> None:
    put("toss_client_id", client_id)
    put("toss_client_secret", client_secret)


def get_toss() -> tuple[str, str] | None:
    return _pair("toss", "client_id", "client_secret")


def delete_toss() -> None:
    delete("toss_client_id")
    delete("toss_client_secret")


# Claude
def get_anthropic() -> str | None:
    return get("anthropic_api_key")
