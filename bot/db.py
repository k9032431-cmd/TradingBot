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

    async def wipe_user(self, user_id: int) -> None:
        await self.conn.execute("DELETE FROM entries WHERE user_id = ?", (user_id,))
        await self.conn.execute("DELETE FROM alerts WHERE user_id = ?", (user_id,))
        await self.conn.commit()
