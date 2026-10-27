"""SQLite 저장소: 주문, 체결, 가상매매 잔고, 자산 기록, 로그, 일봉 캐시."""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

SCHEMA = """
create table if not exists orders (
  id integer primary key autoincrement,
  ts text not null,
  trade_date text not null,
  account text not null,
  market text not null check (market in ('KR','US')),
  symbol text not null,
  side text not null check (side in ('buy','sell')),
  qty integer not null check (qty > 0),
  price real not null,
  status text not null check (status in ('submitted','filled','rejected','dry_run')),
  broker_order_id text,
  strategy text,
  message text
);
create index if not exists orders_day on orders (account, market, trade_date, symbol);

create table if not exists positions (
  account text not null,
  market text not null,
  symbol text not null,
  qty integer not null,
  avg_price real not null,
  primary key (account, market, symbol)
);

create table if not exists cash (
  account text not null,
  market text not null,
  amount real not null,
  primary key (account, market)
);

create table if not exists equity (
  ts text not null,
  trade_date text not null,
  account text not null,
  market text not null,
  equity real not null,
  cash real not null
);
create index if not exists equity_day on equity (account, market, trade_date);

create table if not exists runs (
  trade_date text not null,
  account text not null,
  market text not null,
  ts text not null,
  primary key (trade_date, account, market)
);

create table if not exists events (
  id integer primary key autoincrement,
  ts text not null,
  level text not null,
  source text not null,
  message text not null
);

create table if not exists bars (
  market text not null,
  symbol text not null,
  date text not null,
  open real, high real, low real, close real, volume real,
  primary key (market, symbol, date)
);

create table if not exists ai_runs (
  id text primary key,
  symbol text not null,
  market text not null,
  trade_date text not null,
  started_at text not null,
  finished_at text,
  status text not null check (status in ('running','done','error')),
  rating text,
  confidence real,
  final text,
  reports text,
  tokens_in integer default 0,
  tokens_out integer default 0,
  error text
);
create index if not exists ai_runs_symbol on ai_runs (symbol, trade_date);

create table if not exists approvals (
  id integer primary key autoincrement,
  created_at text not null,
  trade_date text not null,
  account text not null,
  market text not null,
  symbol text not null,
  side text not null,
  qty integer not null,
  price real not null,
  strategy text,
  reason text,
  status text not null default 'pending' check (status in ('pending','approved','rejected','expired')),
  decided_at text,
  result text
);
create index if not exists approvals_status on approvals (status, trade_date);

create table if not exists plans (
  account text not null,
  market text not null,
  symbol text not null,
  run_id text,
  entry real not null,
  stop real not null,
  take real not null,
  created_at text not null,
  closed_at text,
  close_reason text
);
create index if not exists plans_open on plans (account, market, closed_at);

create table if not exists state (
  key text primary key,
  value text not null
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._lock = threading.Lock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        # 예전 DB 에 없는 열 추가 (보유 기한)
        cols = {r[1] for r in self.db.execute("pragma table_info(plans)")}
        if "horizon" not in cols:
            self.db.execute("alter table plans add column horizon integer")
        self.db.commit()

    def exec(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            return cur

    def rows(self, sql: str, args: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    # 로그
    def log(self, level: str, source: str, message: str) -> None:
        self.exec("insert into events (ts, level, source, message) values (?,?,?,?)",
                  (now(), level, source, message))

    def events(self, limit: int = 200) -> list[dict]:
        return self.rows("select * from events order by id desc limit ?", (limit,))

    # 상태 값 (킬 스위치 등)
    def get(self, key: str, default: str | None = None) -> str | None:
        r = self.rows("select value from state where key=?", (key,))
        return r[0]["value"] if r else default

    def set(self, key: str, value: str) -> None:
        self.exec("insert into state (key, value) values (?,?) on conflict(key) do update set value=excluded.value",
                  (key, value))

    # 주문
    def add_order(self, **o) -> int:
        cols = ",".join(o)
        q = ",".join("?" * len(o))
        return self.exec(f"insert into orders (ts,{cols}) values (?,{q})", (now(), *o.values())).lastrowid

    def update_order(self, oid: int, status: str, note: str = "") -> None:
        self.exec("update orders set status=?, message=coalesce(message,'') || ? where id=?",
                  (status, f" | {note}" if note else "", oid))

    def update_fill(self, oid: int, qty: int, price: float) -> None:
        """부분 체결이면 실제로 체결된 수량·평균가로 바꾼다."""
        self.exec("update orders set qty=?, price=? where id=?", (int(qty), float(price), oid))

    def open_orders(self, account: str) -> list[dict]:
        return self.rows("select * from orders where account=? and status='submitted' and broker_order_id is not null "
                         "and broker_order_id != '' order by id", (account,))

    def has_equity(self, account: str, market: str, trade_date: str) -> bool:
        return bool(self.rows("select 1 from equity where account=? and market=? and trade_date=?", (account, market, trade_date)))

    def orders(self, limit: int = 200) -> list[dict]:
        return self.rows("select * from orders order by id desc limit ?", (limit,))

    def ordered_today(self, account: str, market: str, trade_date: str) -> set[str]:
        r = self.rows("select distinct symbol from orders where account=? and market=? and trade_date=? "
                      "and status in ('submitted','filled')", (account, market, trade_date))
        return {x["symbol"] for x in r}

    # 실행 기록 (하루 한 번)
    def ran(self, trade_date: str, account: str, market: str) -> bool:
        return bool(self.rows("select 1 from runs where trade_date=? and account=? and market=?",
                              (trade_date, account, market)))

    def mark_run(self, trade_date: str, account: str, market: str) -> None:
        self.exec("insert or ignore into runs values (?,?,?,?)", (trade_date, account, market, now()))

    # 자산
    def add_equity(self, trade_date: str, account: str, market: str, equity: float, cash: float) -> None:
        self.exec("insert into equity values (?,?,?,?,?,?)", (now(), trade_date, account, market, equity, cash))

    def day_start_equity(self, account: str, market: str, trade_date: str) -> float | None:
        r = self.rows("select equity from equity where account=? and market=? and trade_date<? "
                      "order by ts desc limit 1", (account, market, trade_date))
        return r[0]["equity"] if r else None

    def equity_history(self, account: str, market: str) -> list[dict]:
        return self.rows("select trade_date, max(ts) ts, equity, cash from equity where account=? and market=? "
                         "group by trade_date order by trade_date", (account, market))

    # 승인 대기 주문
    def add_approval(self, **a) -> int:
        cols = ",".join(a)
        return self.exec(f"insert into approvals (created_at,{cols}) values (?,{','.join('?' * len(a))})",
                         (now(), *a.values())).lastrowid

    def approvals(self, status: str | None = None, limit: int = 100) -> list[dict]:
        if status:
            return self.rows("select * from approvals where status=? order by id desc limit ?", (status, limit))
        return self.rows("select * from approvals order by id desc limit ?", (limit,))

    def decide_approval(self, aid: int, status: str, result: str = "") -> None:
        self.exec("update approvals set status=?, decided_at=?, result=? where id=?", (status, now(), result, aid))

    def expire_approvals(self, market: str, today: str) -> int:
        return self.exec("update approvals set status='expired', decided_at=? where status='pending' and market=? "
                         "and trade_date<?", (now(), market, today)).rowcount

    def pending_symbols(self, account: str, market: str, today: str) -> set[str]:
        r = self.rows("select distinct symbol from approvals where account=? and market=? and trade_date=? "
                      "and status='pending'", (account, market, today))
        return {x["symbol"] for x in r}

    # 포지션 계획 (손절·익절)
    def save_plan(self, account, market, symbol, run_id, entry, stop, take, horizon: int | None = None) -> None:
        self.close_plan(account, market, symbol, "replaced")
        self.exec("insert into plans (account, market, symbol, run_id, entry, stop, take, created_at, horizon) "
                  "values (?,?,?,?,?,?,?,?,?)", (account, market, symbol, run_id, entry, stop, take, now(), horizon))

    def close_plan(self, account, market, symbol, reason) -> None:
        self.exec("update plans set closed_at=?, close_reason=? where account=? and market=? and symbol=? "
                  "and closed_at is null", (now(), reason, account, market, symbol))

    def active_plans(self, account, market=None) -> list[dict]:
        if market:
            return self.rows("select * from plans where account=? and market=? and closed_at is null", (account, market))
        return self.rows("select * from plans where account=? and closed_at is null", (account,))

    def recent_stop(self, account, market, symbol, days: int) -> bool:
        from datetime import datetime, timedelta, timezone
        since = (datetime.now(timezone.utc) - timedelta(days=days * 7 / 5)).isoformat(timespec="seconds")
        return bool(self.rows("select 1 from plans where account=? and market=? and symbol=? and close_reason='stop' "
                              "and closed_at>=?", (account, market, symbol, since)))

    def new_buys_today(self, account, market, today) -> int:
        r = self.rows("select count(distinct symbol) n from orders where account=? and market=? and trade_date=? "
                      "and side='buy' and status in ('submitted','filled')", (account, market, today))
        return r[0]["n"] if r else 0

    # AI 회의
    def save_ai_run(self, run: dict) -> None:
        import json
        self.exec(
            "insert into ai_runs (id, symbol, market, trade_date, started_at, finished_at, status, rating, confidence,"
            " final, reports, tokens_in, tokens_out, error) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " on conflict(id) do update set finished_at=excluded.finished_at, status=excluded.status,"
            " rating=excluded.rating, confidence=excluded.confidence, final=excluded.final, reports=excluded.reports,"
            " tokens_in=excluded.tokens_in, tokens_out=excluded.tokens_out, error=excluded.error",
            (run["id"], run["symbol"], run["market"], run["trade_date"], run["started_at"], run.get("finished_at"),
             run["status"], (run.get("final") or {}).get("rating"), (run.get("final") or {}).get("confidence"),
             json.dumps(run.get("final"), ensure_ascii=False),
             json.dumps({k: run.get(k) for k in ("agents", "check", "facts", "stages", "name", "debate", "risk_debate",
                                                 "plan", "checklist", "mode", "cost_usd", "calls", "cache_read")},
                        ensure_ascii=False),
             run.get("tokens_in", 0), run.get("tokens_out", 0), run.get("error")))

    def ai_runs(self, limit: int = 50, symbol: str | None = None) -> list[dict]:
        import json
        q = "select * from ai_runs" + (" where symbol=?" if symbol else "") + " order by started_at desc limit ?"
        rows = self.rows(q, ((symbol, limit) if symbol else (limit,)))
        for r in rows:
            r["final"] = json.loads(r["final"]) if r.get("final") else None
            extra = json.loads(r.pop("reports")) if r.get("reports") else {}
            if extra and "agents" not in extra:  # 예전 형식: 참석자 보고서만 저장
                extra = {"agents": extra}
            r.update({k: v for k, v in extra.items() if v is not None})
            r.setdefault("agents", {})
            r.setdefault("name", r["symbol"])
        return rows

    def latest_decision(self, symbol: str, since_date: str) -> dict | None:
        import json
        r = self.rows("select * from ai_runs where symbol=? and status='done' and trade_date>=? "
                      "order by finished_at desc limit 1", (symbol, since_date))
        if not r:
            return None
        d = r[0]
        d["final"] = json.loads(d["final"]) if d.get("final") else None
        extra = json.loads(d.pop("reports")) if d.get("reports") else {}
        if isinstance(extra, dict) and "agents" in extra:
            d.update({k: extra.get(k) for k in ("check", "plan", "checklist")})
        return d

    # 일봉 캐시
    def save_bars(self, market: str, symbol: str, df: pd.DataFrame) -> None:
        rows = [(market, symbol, d.strftime("%Y-%m-%d"), r.open, r.high, r.low, r.close, r.volume)
                for d, r in df.iterrows()]
        with self._lock:
            self.db.executemany("insert or replace into bars values (?,?,?,?,?,?,?,?)", rows)
            self.db.commit()

    def load_bars(self, market: str, symbol: str) -> pd.DataFrame:
        r = self.rows("select date, open, high, low, close, volume from bars where market=? and symbol=? "
                      "order by date", (market, symbol))
        if not r:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        df = pd.DataFrame(r)
        df.index = pd.to_datetime(df.pop("date"))
        return df
