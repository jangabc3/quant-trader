"""명령줄: qtrader serve | backtest | run | demo-data | set-keys | kill"""
from __future__ import annotations

import argparse
import getpass
import json
import sys
import threading
import webbrowser

from . import backtest, config as config_mod, strategies, vault
from .data import panel, write_demo_csv
from .engine import build_context, run_market
from .models import Market


def cmd_serve(a):
    import uvicorn

    from .scheduler import Scheduler
    from .web.app import create_app

    sched = Scheduler()
    sched.start()
    url = f"http://127.0.0.1:{a.port}"
    print(f"QTrader 대시보드: {url}  (끄려면 Ctrl+C)")
    if not a.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(sched), host="127.0.0.1", port=a.port, log_level="warning")


def cmd_backtest(a):
    ctx = build_context()
    m = Market(a.market)
    mc = ctx.cfg["markets"][m.value]
    uni = a.symbols.split(",") if a.symbols else mc["universe"]
    errors: list[str] = []
    p = panel(ctx.data, m, uni, a.days, errors)
    for e in errors:
        print("데이터 실패:", e, file=sys.stderr)
    if not p:
        sys.exit("데이터가 없습니다")
    name = a.strategy or mc["strategy"]
    params = json.loads(a.params) if a.params else (mc.get("params") if name == mc["strategy"] else {})
    r = backtest.run(strategies.build(name, params), p, m,
                     max_weight=ctx.cfg["risk"]["max_weight"], max_exposure=ctx.cfg["risk"]["max_exposure"])
    print(json.dumps({"strategy": name, "metrics": r.metrics, "last_weights": r.to_dict()["last_weights"]},
                     ensure_ascii=False, indent=2))


def cmd_run(a):
    ctx = build_context()
    if ctx.broker.live and not a.dry_run:
        if input("실전 계좌로 주문합니다. 진행하려면 '실전주문' 입력: ").strip() != "실전주문":
            sys.exit("취소했습니다")
    print(json.dumps(run_market(ctx, Market(a.market), dry_run=a.dry_run, force=a.force),
                     ensure_ascii=False, indent=2))


def cmd_demo_data(a):
    paths = write_demo_csv(config_mod.home() / "data", config_mod.load())
    print(f"체험용 합성 시세 {len(paths)}개를 만들었습니다: {config_mod.home() / 'data'}")
    print("주의: 실제 시세가 아닙니다. 화면과 전략 동작을 확인하는 용도입니다.")


def cmd_set_keys(a):
    key = getpass.getpass(f"{a.env} 앱키: ")
    sec = getpass.getpass(f"{a.env} 앱시크릿: ")
    vault.set_kis(a.env, key, sec)
    cfg = config_mod.load()
    if a.account:
        cfg["kis"]["account"] = a.account
        config_mod.save(cfg)
    print("저장했습니다 (OS 자격 증명 저장소).")


def cmd_kill(a):
    ctx = build_context()
    ctx.store.set("kill_switch", "1" if a.state == "on" else "0")
    ctx.store.log("warn", "cli", f"킬 스위치 {a.state}")
    print(f"킬 스위치 {a.state}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="qtrader", description="국내·미국 주식 퀀트 자동매매")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="대시보드와 자동매매 스케줄러 실행")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--no-browser", action="store_true")
    s.set_defaults(fn=cmd_serve)

    s = sub.add_parser("backtest", help="백테스트")
    s.add_argument("--market", default="KR", choices=["KR", "US"])
    s.add_argument("--strategy", choices=list(strategies.REGISTRY))
    s.add_argument("--params", help='JSON, 예: \'{"top_k": 3}\'')
    s.add_argument("--symbols", help="쉼표로 구분")
    s.add_argument("--days", type=int, default=750)
    s.set_defaults(fn=cmd_backtest)

    s = sub.add_parser("run", help="지금 한 번 실행")
    s.add_argument("--market", default="KR", choices=["KR", "US"])
    s.add_argument("--dry-run", action="store_true", help="주문하지 않고 계산만")
    s.add_argument("--force", action="store_true", help="오늘 이미 실행했어도 다시")
    s.set_defaults(fn=cmd_run)

    s = sub.add_parser("demo-data", help="키 없이 체험할 합성 시세 CSV 생성")
    s.set_defaults(fn=cmd_demo_data)

    s = sub.add_parser("set-keys", help="KIS 앱키 저장")
    s.add_argument("--env", default="demo", choices=["demo", "real"])
    s.add_argument("--account", help="12345678-01")
    s.set_defaults(fn=cmd_set_keys)

    s = sub.add_parser("kill", help="킬 스위치")
    s.add_argument("state", choices=["on", "off"])
    s.set_defaults(fn=cmd_kill)

    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
