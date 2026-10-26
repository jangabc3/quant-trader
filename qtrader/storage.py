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
