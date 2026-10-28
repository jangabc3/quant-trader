"""프로그램 창(desktop): 창 띄우기, 서버 두 번 안 켜기, 창 엔진이 없을 때 브라우저, 서버 다시 켜기·업데이트."""
from qtrader import desktop


class FakeWin:
    def __init__(self):
        self.destroyed = False

    def destroy(self):
        self.destroyed = True


class FakeWebview:
    def __init__(self):
        self.created, self.started = None, None

    def create_window(self, title, url, **kw):
        self.created = (title, url, kw)
        return FakeWin()

    def start(self, **kw):
        self.started = kw


class FakeSup:
    made = []

    def __init__(self, port, on_update=None):
        self.port, self.on_update, self.started, self.stopped = port, on_update, False, False
        FakeSup.made.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def join(self):
        pass


def test_opens_own_window_and_stops_server_on_close():
    FakeSup.made.clear()
    wv, ups = FakeWebview(), iter([False, False, True])
    r = desktop.run(8765, webview=wv, supervisor_cls=FakeSup, up=lambda p: next(ups, True), wait_s=5)
    assert r == "window"
    title, url, kw = wv.created
    assert title == "QTrader" and url == "http://127.0.0.1:8765" and kw["confirm_close"] is True
    assert "자동매매" in wv.started["localization"]["global.quitConfirmation"] and wv.started["private_mode"] is False
    sup = FakeSup.made[0]
    assert sup.started and sup.stopped  # 창을 닫으면 서버도 끈다


def test_second_launch_reuses_running_server():
    """이미 돌고 있으면 서버를 또 켜지 않는다 (자동매매가 두 번 돌지 않게). 그 창은 닫아도 서버는 그대로."""
    FakeSup.made.clear()
    wv = FakeWebview()
    assert desktop.run(8765, webview=wv, supervisor_cls=FakeSup, up=lambda p: True) == "window"
    assert FakeSup.made == [] and wv.created[2]["confirm_close"] is False


def test_falls_back_to_browser_without_window_engine(monkeypatch):
    opened = []
    monkeypatch.setattr(desktop.webbrowser, "open", opened.append)

    class Broken(FakeWebview):
        def start(self, **kw):
            raise RuntimeError("WebView2 없음")
    assert desktop.run(8765, webview=Broken(), supervisor_cls=FakeSup, up=lambda p: True) == "error"
    assert opened == ["http://127.0.0.1:8765"]


class FakeProc:
    def __init__(self, code):
        self.code = code

    def wait(self, timeout=None):
        return self.code

    def poll(self):
        return self.code


def test_supervisor_restarts_after_crash_then_ends(isolated_home):
    codes, slept, calls = iter([1, 0]), [], []

    def popen(args, **kw):
        calls.append(args)
        return FakeProc(next(codes))
    s = desktop.Supervisor(8765, popen=popen, sleep=slept.append)
    s.run()
    assert len(calls) == 2 and slept == [10] and calls[0][-3:] == ["--port", "8765", "--no-browser"]


def test_supervisor_closes_window_for_update(isolated_home, monkeypatch):
    from qtrader import updater
    monkeypatch.setattr(updater, "finish_pending", lambda home: True)
    win = FakeWin()
    s = desktop.Supervisor(8765, on_update=win.destroy, popen=lambda a, **k: FakeProc(updater.RESTART_CODE), sleep=lambda s: None)
    s.run()
    assert win.destroyed  # 창을 닫아야 파일을 바꾸는 스크립트가 QTrader.exe 를 덮어쓸 수 있다
