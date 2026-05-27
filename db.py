import aiosqlite
from config import DB_PATH


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id     INTEGER PRIMARY KEY,
                username    TEXT,
                first_name  TEXT,
                last_seen   TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id     INTEGER NOT NULL,
                user_id     INTEGER NOT NULL,
                dep_code    TEXT NOT NULL,
                dep_name    TEXT NOT NULL,
                arv_code    TEXT NOT NULL,
                arv_name    TEXT NOT NULL,
                date        TEXT NOT NULL,
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                active      INTEGER NOT NULL DEFAULT 1
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS snapshots (
                subscription_id INTEGER NOT NULL,
                train_number    TEXT NOT NULL,
                car_type        TEXT NOT NULL,
                free_seats      INTEGER NOT NULL,
                PRIMARY KEY (subscription_id, train_number, car_type),
                FOREIGN KEY (subscription_id) REFERENCES subscriptions(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS forward_map (
                forwarded_msg_id INTEGER PRIMARY KEY,
                original_chat_id INTEGER NOT NULL,
                created_at       TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        # Migrations for existing DBs
        for migration in [
            "ALTER TABLE users ADD COLUMN blocked INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE users ADD COLUMN language TEXT NOT NULL DEFAULT 'en'",
        ]:
            try:
                await db.execute(migration)
                await db.commit()
            except Exception:
                pass
        await db.commit()


async def upsert_user(user_id: int, username: str | None, first_name: str | None) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO users (user_id, username, first_name, last_seen)
               VALUES (?, ?, ?, datetime('now'))
               ON CONFLICT(user_id) DO UPDATE SET
                   username   = excluded.username,
                   first_name = excluded.first_name,
                   last_seen  = excluded.last_seen""",
            (user_id, username, first_name),
        )
        await db.commit()


async def update_subscription_codes(sub_id: int, dep_code: str, arv_code: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE subscriptions SET dep_code = ?, arv_code = ? WHERE id = ?",
            (dep_code, arv_code, sub_id),
        )
        await db.commit()


async def add_subscription(
    chat_id: int,
    user_id: int,
    dep_code: str,
    dep_name: str,
    arv_code: str,
    arv_name: str,
    date: str,
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO subscriptions
               (chat_id, user_id, dep_code, dep_name, arv_code, arv_name, date)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (chat_id, user_id, dep_code, dep_name, arv_code, arv_name, date),
        )
        await db.commit()
        return cursor.lastrowid


async def get_user_subscriptions(user_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM subscriptions WHERE user_id = ? AND active = 1 ORDER BY date",
            (user_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_all_active() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT s.*, COALESCE(u.language, 'en') AS language
            FROM subscriptions s
            LEFT JOIN users u ON s.user_id = u.user_id
            WHERE s.active = 1
        """) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def deactivate(sub_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM snapshots WHERE subscription_id = ?", (sub_id,))
        await db.execute("UPDATE subscriptions SET active = 0 WHERE id = ?", (sub_id,))
        await db.commit()


async def activate(sub_id: int) -> bool:
    """Set subscription active=1. Returns True if the row existed."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "UPDATE subscriptions SET active = 1 WHERE id = ?", (sub_id,)
        )
        await db.commit()
        return cur.rowcount > 0


async def get_subscription(sub_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT s.*, u.username, u.first_name FROM subscriptions s "
            "LEFT JOIN users u ON s.user_id = u.user_id WHERE s.id = ?",
            (sub_id,),
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def get_snapshot(sub_id: int) -> dict[str, dict[str, int]]:
    """Returns {train_number: {car_type: free_seats}}."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT train_number, car_type, free_seats FROM snapshots WHERE subscription_id = ?",
            (sub_id,),
        ) as cur:
            rows = await cur.fetchall()

    result: dict[str, dict[str, int]] = {}
    for row in rows:
        result.setdefault(row["train_number"], {})[row["car_type"]] = row["free_seats"]
    return result


async def get_stats() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(DISTINCT user_id) FROM subscriptions WHERE active = 1") as cur:
            total_users = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM subscriptions WHERE active = 1") as cur:
            active_watches = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM subscriptions") as cur:
            total_watches = (await cur.fetchone())[0]
    return {
        "total_users": total_users,
        "active_watches": active_watches,
        "total_watches_ever": total_watches,
    }


async def get_all_watches() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT s.*, u.username, u.first_name
            FROM subscriptions s
            LEFT JOIN users u ON s.user_id = u.user_id
            WHERE s.active = 1
            ORDER BY u.username, s.date
        """) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def set_user_language(user_id: int, language: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET language = ? WHERE user_id = ?",
            (language, user_id),
        )
        await db.commit()


async def get_user_language(user_id: int) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT language FROM users WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row else "en"


async def set_user_blocked(user_id: int, blocked: bool) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET blocked = ? WHERE user_id = ?",
            (1 if blocked else 0, user_id),
        )
        await db.commit()


async def get_all_users() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT u.user_id, u.username, u.first_name, u.last_seen,
                   u.blocked, COUNT(s.id) as watch_count
            FROM users u
            LEFT JOIN subscriptions s ON u.user_id = s.user_id AND s.active = 1
            GROUP BY u.user_id
            ORDER BY u.last_seen DESC
        """) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_all_user_ids() -> list[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM users") as cur:
            return [r[0] for r in await cur.fetchall()]


async def save_forward_map(forwarded_msg_id: int, original_chat_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO forward_map (forwarded_msg_id, original_chat_id) VALUES (?, ?)",
            (forwarded_msg_id, original_chat_id),
        )
        await db.execute(
            "DELETE FROM forward_map WHERE created_at < datetime('now', '-7 days')"
        )
        await db.commit()


async def get_forward_chat(forwarded_msg_id: int) -> int | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT original_chat_id FROM forward_map WHERE forwarded_msg_id = ?",
            (forwarded_msg_id,),
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row else None


async def save_snapshot(sub_id: int, snapshot: dict[str, dict[str, int]]) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM snapshots WHERE subscription_id = ?", (sub_id,)
        )
        for train_num, cars in snapshot.items():
            for car_type, free_seats in cars.items():
                await db.execute(
                    """INSERT INTO snapshots
                       (subscription_id, train_number, car_type, free_seats)
                       VALUES (?, ?, ?, ?)""",
                    (sub_id, train_num, car_type, free_seats),
                )
        await db.commit()
