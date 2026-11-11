"""데스크톱 창: QTrader.exe 를 누르면 브라우저 탭이 아니라 자기 창으로 뜬다.

- 창은 pywebview (윈도우에 기본으로 있는 Edge WebView2) 로 같은 화면(http://127.0.0.1:포트)을 띄운다.
- 자동매매 서버(serve)는 따로 자식 프로세스로 돌리고, 오류로 꺼지면 다시 켠다 (cli.supervise 와 같은 규칙).
- 이미 다른 QTrader 가 그 포트에서 돌고 있으면 서버를 또 켜지 않고 창만 연다 (자동매매가 두 번 돌지 않게).
- 창의 X 를 누르면 끄지 않고 작업 표시줄 오른쪽 '숨겨진 아이콘'(^) 으로 숨는다. 자동매매는 계속 돈다.
  그 아이콘을 두 번 누르면 창이 다시 뜨고, 오른쪽 클릭 → '끄기' 로만 완전히 끈다 (윈도우 메시지 상자로 확인, 6초 안에 안 꺼지면 강제로).
- 켜져 있는 동안 PC 가 알아서 절전 모드로 들어가지 않게 한다 (화면은 꺼져도 된다).
- 아이콘 기능(pystray)을 못 쓰면 예전처럼 X 로 끄되 확인을 받는다.
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
HIDDEN_TIP = ("QTrader 는 계속 돌아가요. 작업 표시줄 오른쪽 ^ (숨겨진 아이콘) 에서 두 번 누르면 다시 열려요. "
              "완전히 끄려면 아이콘을 오른쪽 클릭 → 끄기.")


def keep_awake(on: bool = True) -> bool:
    """윈도우가 알아서 절전 모드로 들어가지 않게 (ES_CONTINUOUS | ES_SYSTEM_REQUIRED). 부른 스레드가 살아 있는 동안 유지.
    화면 끄기는 막지 않는다. 노트북 덮개를 닫으면 윈도우 설정에 따라 잠들 수 있다."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        flags = 0x80000000 | (0x00000001 if on else 0)
        return bool(ctypes.windll.kernel32.SetThreadExecutionState(flags))
    except Exception:
        return False


def make_tray(on_open, on_quit):
    """작업 표시줄 아이콘. pystray·Pillow 가 없으면 None (그럼 X 로 끈다)."""
    try:
        import pystray
        from PIL import Image
    except Exception:
        return None
    from pathlib import Path
    try:
        img = Image.open(Path(__file__).parent / "web" / "static" / "qtrader.ico")
    except Exception:
        img = Image.new("RGB", (64, 64), "#3182f6")
    menu = pystray.Menu(pystray.MenuItem("QTrader 열기", lambda *a: on_open(), default=True),
                        pystray.MenuItem("끄기 (자동매매 멈춤)", lambda *a: on_quit()))
    return pystray.Icon("QTrader", img, "QTrader · 자동매매 켜짐", menu)


def confirm_quit(win) -> bool:
    """끌지 묻는다. 윈도우 기본 메시지 상자(맨 앞에 뜸)를 쓴다.
    창이 숨겨진 채 아이콘 메뉴에서 누르면 pywebview 확인 창이 안 뜨거나 바로 '아니요'가 돼서 안 꺼지던 문제가 있었다."""
    if sys.platform == "win32":
        try:
            import ctypes
            MB_YESNO, MB_ICONQUESTION, MB_TOPMOST, MB_SETFOREGROUND, IDYES = 0x4, 0x20, 0x40000, 0x10000, 6
            return ctypes.windll.user32.MessageBoxW(0, QUIT_ASK, "QTrader 끄기", MB_YESNO | MB_ICONQUESTION | MB_TOPMOST | MB_SETFOREGROUND) == IDYES
        except Exception:
            pass
    try:
        return bool(win.create_confirmation_dialog("QTrader 끄기", QUIT_ASK)) if win else True
    except Exception:
        return True


class Tray:
    """X 를 누르면 창을 숨기고 아이콘으로. '끄기' 를 눌렀을 때만 창을 닫는다."""

    def __init__(self, maker=make_tray, confirm=confirm_quit):
        self.win, self.quitting, self.told = None, False, False
        self.confirm = confirm
        self.force_exit = None  # run() 이 정한다: 창이 끝내 안 닫히면 몇 초 뒤 강제로 정리하고 끝낸다
        self.icon = maker(self.open, self.quit)

    @property
    def ok(self) -> bool:
        return self.icon is not None

    def start(self, win) -> None:
        self.win = win
        win.events.closing += self.on_closing
        self.icon.run_detached()

    def on_closing(self):
        if self.quitting:
            return True
        threading.Thread(target=self.win.hide, daemon=True).start()  # 창 스레드 밖에서 숨긴다
        if not self.told:
            self.told = True
            try:
                self.icon.notify(HIDDEN_TIP, "QTrader 는 계속 돌아가요")
            except Exception:
                pass
        return False  # 닫기 취소

    def open(self) -> None:
        if self.win:
            self.win.show()
            try:
                self.win.restore()
            except Exception:
                pass

    def quit(self, ask=True) -> None:
        if ask and not self.confirm(self.win):
            return
        self.close()

    def close(self) -> None:
        if self.quitting:
            return
        self.quitting = True
        if self.force_exit:
            threading.Timer(6.0, self.force_exit).start()  # 6초 안에 정상으로 안 끝나면 강제로
        try:
            self.icon.stop()
        except Exception:
            pass
        if self.win:
            try:
                self.win.destroy()
            except Exception:
                pass


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


def run(port: int = 8765, webview=None, supervisor_cls=Supervisor, up=server_up, wait_s: float = 60.0, tray_cls=Tray) -> str:
    """창을 띄우고, 창이 닫힐 때까지 기다린다. 돌려주는 값은 시험용: 'window' / 'browser' / 'error'."""
    from . import config as config_mod
    url = f"http://127.0.0.1:{port}"
    real = webview is None  # 시험에서는 강제 종료를 걸지 않는다
    owner = not up(port)  # 이미 돌고 있으면 그 서버를 쓴다
    win = {}
    sup, tray = None, None
    done = threading.Event()

    def cleanup():
        """사용자가 끈 것으로 적고 서버를 멈춘다 (한 번만). 업데이트로 닫혔으면 이미 'update' 로 적혀 있어 덮어쓰지 않는다."""
        if done.is_set() or not sup:
            return
        done.set()
        try:
            from .scheduler import mark_planned_stop
            from .storage import Store
            st = Store(config_mod.home() / "qtrader.db")
            if '"update"' not in (st.get("planned_stop") or ""):
                mark_planned_stop(st, "quit")
        except Exception:
            pass
        sup.stop()
    if owner:
        keep_awake(True)
        tray = tray_cls()
        if not tray.ok:
            tray = None
        sup = supervisor_cls(port, on_update=lambda: tray.close() if tray else (win.get("w") and win["w"].destroy()))
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
                                  confirm_close=owner and tray is None, background_color="#0b0f14", text_select=True)
        win["w"] = w
        if tray:
            if real:
                import os

                def force():  # 창이 안 닫혀도 서버를 끄고 프로그램을 끝낸다
                    cleanup()
                    os._exit(0)
                tray.force_exit = force
            tray.start(w)
        store = config_mod.home() / "webview"  # 화면 설정(원/달러 보기 등)이 다음에 켤 때도 남게
        webview.start(localization={"global.quitConfirmation": QUIT_ASK}, private_mode=False, storage_path=str(store))
    except Exception:
        if tray:
            tray.close()
        webbrowser.open(url)  # 창 엔진(WebView2)이 없거나 실패하면 브라우저로
        if sup:
            sup.join()
        return "error"
    if tray:
        tray.close()
    cleanup()
    return "window"
