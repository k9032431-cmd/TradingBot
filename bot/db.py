import time
from pathlib import Path

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    currency TEXT NOT NULL DEFAULT 'USDT',
    start_balance REAL NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    kind TEXT NOT NULL,            -- trade | expense | deposit | withdraw
    amount REAL NOT NULL,          -- со знаком: + прибыль / - убыток
    symbol TEXT,
    category TEXT,
    note TEXT,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entries_user ON entries(user_id, created_at);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    target REAL NOT NULL,
    direction TEXT NOT NULL,       -- above | below
    note TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL,
    triggered_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_alerts_active ON alerts(active);
CREATE TABLE IF NOT EXISTS api_keys (
    user_id INTEGER NOT NULL,
    exchange TEXT NOT NULL,        -- bybit
    api_key TEXT NOT NULL,
    secret_enc TEXT NOT NULL,      -- секрет зашифрован (см. crypto.py)
    last_sync_ms INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, exchange)
);
"""


class DB:
    def __init__(self, path: str):
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.executescript(SCHEMA)
        # миграция: ext_id — ID записи на бирже, чтобы не импортировать дважды
        cur = await self.conn.execute("PRAGMA table_info(entries)")
        if "ext_id" not in {r["name"] for r in await cur.fetchall()}:
            await self.conn.execute("ALTER TABLE entries ADD COLUMN ext_id TEXT")
        await self.conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_entries_ext ON entries(user_id, ext_id) WHERE ext_id IS NOT NULL"
        )
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()

    # ---------- users ----------
    async def ensure_user(self, user_id: int) -> aiosqlite.Row:
        await self.conn.execute(
            "INSERT OR IGNORE INTO users (id, created_at) VALUES (?, ?)", (user_id, int(time.time()))
        )
        await self.conn.commit()
        cur = await self.conn.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        return await cur.fetchone()

    async def set_user(self, user_id: int, **fields) -> None:
        cols = ", ".join(f"{k} = ?" for k in fields)
        await self.conn.execute(f"UPDATE users SET {cols} WHERE id = ?", (*fields.values(), user_id))
        await self.conn.commit()

    # ---------- entries ----------
    async def add_entry(self, user_id: int, kind: str, amount: float, symbol: str | None = None,
                        category: str | None = None, note: str | None = None) -> int:
        cur = await self.conn.execute(
            "INSERT INTO entries (user_id, kind, amount, symbol, category, note, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, kind, amount, symbol, category, note, int(time.time())),
        )
        await self.conn.commit()
        return cur.lastrowid

    async def import_entry(self, user_id: int, ext_id: str, kind: str, amount: float, created_at: int,
                           symbol: str | None = None, category: str | None = None,
                           note: str | None = None) -> bool:
        """Запись с биржи. False — уже была импортирована."""
        cur = await self.conn.execute(
            "INSERT OR IGNORE INTO entries (user_id, kind, amount, symbol, category, note, created_at, ext_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, kind, amount, symbol, category, note, created_at, ext_id),
        )
        return cur.rowcount > 0

    async def accumulate_entry(self, user_id: int, ext_id: str, kind: str, amount: float, created_at: int,
                               category: str | None = None, note: str | None = None) -> None:
        """Сводная запись за день (комиссии, фандинг): сумма накапливается."""
        await self.conn.execute(
            "INSERT INTO entries (user_id, kind, amount, category, note, created_at, ext_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id, ext_id) WHERE ext_id IS NOT NULL DO UPDATE SET amount = amount + excluded.amount",
            (user_id, kind, amount, category, note, created_at, ext_id),
        )

    async def drop_daily_imports(self, user_id: int, prefixes: tuple[str, ...], since: int) -> None:
        """Перед полным переимпортом убираем сводные записи, иначе суммы задвоятся."""
        for p in prefixes:
            await self.conn.execute(
                "DELETE FROM entries WHERE user_id = ? AND ext_id LIKE ? AND created_at >= ?",
                (user_id, p + "%", since),
            )

    async def commit(self) -> None:
        await self.conn.commit()

    async def entries(self, user_id: int, since: int = 0, limit: int | None = None,
                      offset: int = 0) -> list[aiosqlite.Row]:
        q = "SELECT * FROM entries WHERE user_id = ? AND created_at >= ? ORDER BY created_at DESC, id DESC"
        args: tuple = (user_id, since)
        if limit is not None:
            q += " LIMIT ? OFFSET ?"
            args += (limit, offset)
        cur = await self.conn.execute(q, args)
        return await cur.fetchall()

    async def count_entries(self, user_id: int) -> int:
        cur = await self.conn.execute("SELECT COUNT(*) FROM entries WHERE user_id = ?", (user_id,))
        return (await cur.fetchone())[0]

    async def delete_entry(self, user_id: int, entry_id: int) -> bool:
        cur = await self.conn.execute(
            "DELETE FROM entries WHERE id = ? AND user_id = ?", (entry_id, user_id)
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def balance_delta(self, user_id: int) -> float:
        cur = await self.conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM entries WHERE user_id = ?", (user_id,)
        )
        return (await cur.fetchone())[0]

    # ---------- alerts ----------
    async def add_alert(self, user_id: int, symbol: str, target: float, direction: str,
                        note: str | None = None) -> int:
        cur = await self.conn.execute(
            "INSERT INTO alerts (user_id, symbol, target, direction, note, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, symbol, target, direction, note, int(time.time())),
        )
        await self.conn.commit()
        return cur.lastrowid

    async def user_alerts(self, user_id: int) -> list[aiosqlite.Row]:
        cur = await self.conn.execute(
            "SELECT * FROM alerts WHERE user_id = ? AND active = 1 ORDER BY symbol, target", (user_id,)
        )
        return await cur.fetchall()

    async def active_alerts(self) -> list[aiosqlite.Row]:
        cur = await self.conn.execute("SELECT * FROM alerts WHERE active = 1")
        return await cur.fetchall()

    async def get_alert(self, alert_id: int) -> aiosqlite.Row | None:
        cur = await self.conn.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,))
        return await cur.fetchone()

    async def trigger_alert(self, alert_id: int) -> None:
        await self.conn.execute(
            "UPDATE alerts SET active = 0, triggered_at = ? WHERE id = ?", (int(time.time()), alert_id)
        )
        await self.conn.commit()

    async def delete_alert(self, user_id: int, alert_id: int) -> bool:
        cur = await self.conn.execute(
            "DELETE FROM alerts WHERE id = ? AND user_id = ?", (alert_id, user_id)
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def delete_all_alerts(self, user_id: int) -> int:
        cur = await self.conn.execute("DELETE FROM alerts WHERE user_id = ? AND active = 1", (user_id,))
        await self.conn.commit()
        return cur.rowcount

    # ---------- API-ключи бирж ----------
    async def set_api(self, user_id: int, exchange: str, api_key: str, secret_enc: str) -> None:
        await self.conn.execute(
            "INSERT OR REPLACE INTO api_keys (user_id, exchange, api_key, secret_enc, last_sync_ms, created_at) "
            "VALUES (?, ?, ?, ?, 0, ?)",
            (user_id, exchange, api_key, secret_enc, int(time.time())),
        )
        await self.conn.commit()

    async def get_api(self, user_id: int, exchange: str) -> aiosqlite.Row | None:
        cur = await self.conn.execute(
            "SELECT * FROM api_keys WHERE user_id = ? AND exchange = ?", (user_id, exchange)
        )
        return await cur.fetchone()

    async def all_apis(self, exchange: str) -> list[aiosqlite.Row]:
        cur = await self.conn.execute("SELECT * FROM api_keys WHERE exchange = ?", (exchange,))
        return await cur.fetchall()

    async def set_api_sync(self, user_id: int, exchange: str, last_sync_ms: int) -> None:
        await self.conn.execute(
            "UPDATE api_keys SET last_sync_ms = ? WHERE user_id = ? AND exchange = ?",
            (last_sync_ms, user_id, exchange),
        )
        await self.conn.commit()

    async def delete_api(self, user_id: int, exchange: str) -> None:
        await self.conn.execute("DELETE FROM api_keys WHERE user_id = ? AND exchange = ?", (user_id, exchange))
        await self.conn.commit()

    async def wipe_user(self, user_id: int) -> None:
        await self.conn.execute("DELETE FROM entries WHERE user_id = ?", (user_id,))
        await self.conn.execute("DELETE FROM alerts WHERE user_id = ?", (user_id,))
        await self.conn.commit()
