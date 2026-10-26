"""KIS API 키 보관. 윈도우에서는 자격 증명 관리자, 맥은 키체인에 들어간다."""
from __future__ import annotations

import keyring

SERVICE = "QTrader"


def _name(env: str, field: str) -> str:
    return f"kis_{env}_{field}"  # env: demo | real


def set_kis(env: str, app_key: str, app_secret: str) -> None:
    keyring.set_password(SERVICE, _name(env, "app_key"), app_key.strip())
    keyring.set_password(SERVICE, _name(env, "app_secret"), app_secret.strip())


def get_kis(env: str) -> tuple[str, str] | None:
    k = keyring.get_password(SERVICE, _name(env, "app_key"))
    s = keyring.get_password(SERVICE, _name(env, "app_secret"))
    return (k, s) if k and s else None


def has_kis(env: str) -> bool:
    try:
        return get_kis(env) is not None
    except Exception:
        return False


def delete_kis(env: str) -> None:
    for f in ("app_key", "app_secret"):
        try:
            keyring.delete_password(SERVICE, _name(env, f))
        except keyring.errors.PasswordDeleteError:
            pass
