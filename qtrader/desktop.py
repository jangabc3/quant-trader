"""데스크톱 창: QTrader.exe 를 누르면 브라우저 탭이 아니라 자기 창으로 뜬다.

- 창은 pywebview (윈도우에 기본으로 있는 Edge WebView2) 로 같은 화면(http://127.0.0.1:포트)을 띄운다.
- 자동매매 서버(serve)는 따로 자식 프로세스로 돌리고, 오류로 꺼지면 다시 켠다 (cli.supervise 와 같은 규칙).
- 이미 다른 QTrader 가 그 포트에서 돌고 있으면 서버를 또 켜지 않고 창만 연다 (자동매매가 두 번 돌지 않게).
- 창을 닫으면 확인을 받고 서버도 끈다 (자동매매·손절 감시가 멈춘다는 것을 알린다).
- pywebview 를 쓸 수 없는 PC 면 예전처럼 브라우저로 연다.
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time
import webbrowser

QUIT_ASK = ("QTrader 를 끌까요?\n\n끄면 자동매매와 손절·익절 감시가 멈춰요. "
            "토스 서버에 걸어 둔 손절·익절(OCO)은 PC 가 꺼져 있어도 토스가 지켜 줘요.")


def server_up(port: int, timeout: float = 1.0) -> bool:
    import requests
    try:
        return requests.get(f"http://127.0.0.1:{port}/api/status", timeout=timeout).status_code == 200
    except Exception:
        return False


def serve_args(port: int) -> list[str]:
    base = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, "-m", "qtrader"]
    return base + ["serve", "--port", str(port), "--no-browser"]


class Supervisor(threading.Thread):
    """serve 를 자식 프로세스로 돌린다. 오류로 꺼지면 다시 켜고, 업데이트 준비가 끝나면 on_update 를 부른다."""

    def __init__(self, port: int, on_update=None, popen=subprocess.Popen, sleep=time.sleep):
        super().__init__(name="qtrader-supervisor", daemon=True)
        self.port, self.on_update, self.popen, self.sleep = port, on_update, popen, sleep
        self.proc = None
        self._stop = threading.Event()

    def run(self) -> None:
        from .updater import RESTART_CODE, finish_pending
        from . import config as config_mod
        recent: list[float] = []
        while not self._stop.is_set():
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # 윈도우에서 까만 창이 따로 뜨지 않게
            self.proc = self.popen(serve_args(self.port), creationflags=flags)
            code = self.proc.wait()
            if self._stop.is_set() or code == 0:
                return
            if code == RESTART_CODE:  # 업데이트 준비 끝: 파일 바꾸는 스크립트를 띄우고 창을 닫는다 (스크립트가 다시 켠다)
                if finish_pending(config_mod.home()):
                    if self.on_update:
                        self.on_update()
                    return
                continue
            recent = [t for t in recent if time.time() - t < 600] + [time.time()]
            wait = 300 if len(recent) > 5 else 10  # 10분에 5번 넘게 죽으면 5분 쉬었다가
            try:
                from .storage import Store
                Store(config_mod.home() / "qtrader.db").log("notice", "live", f"프로그램이 오류로 꺼져 {wait}초 뒤 다시 켭니다 (종료 코드 {code})")
            except Exception:
                pass
            self.sleep(wait)

    def stop(self) -> None:
        self._stop.set()
        p = self.proc
        if p is not None and p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=10)
            except Exception:
                p.kill()


def run(port: int = 8765, webview=None, supervisor_cls=Supervisor, up=server_up, wait_s: float = 60.0) -> str:
    """창을 띄우고, 창이 닫힐 때까지 기다린다. 돌려주는 값은 시험용: 'window' / 'browser' / 'error'."""
    from . import config as config_mod
    url = f"http://127.0.0.1:{port}"
    owner = not up(port)  # 이미 돌고 있으면 그 서버를 쓴다
    win = {}
    sup = None
    if owner:
        sup = supervisor_cls(port, on_update=lambda: win.get("w") and win["w"].destroy())
        sup.start()
        end = time.time() + wait_s
        while time.time() < end and not up(port):
            time.sleep(0.3)
    if webview is None:
        try:
            import webview  # noqa: F811  pywebview (윈도우: Edge WebView2)
        except Exception:
            webview = None
    if webview is None:
        webbrowser.open(url)
        if sup:
            sup.join()
        return "browser"
    try:
        w = webview.create_window("QTrader", url, width=1440, height=920, min_size=(1024, 680),
                                  confirm_close=owner, background_color="#0b0f14", text_select=True)
        win["w"] = w
        store = config_mod.home() / "webview"  # 화면 설정(원/달러 보기 등)이 다음에 켤 때도 남게
        webview.start(localization={"global.quitConfirmation": QUIT_ASK}, private_mode=False, storage_path=str(store))
    except Exception:
        webbrowser.open(url)  # 창 엔진(WebView2)이 없거나 실패하면 브라우저로
        if sup:
            sup.join()
        return "error"
    if sup:
        sup.stop()
    return "window"
