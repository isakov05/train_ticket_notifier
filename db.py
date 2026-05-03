import aiosqlite
from config import DB_PATH


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
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
        async with db.execute(
            "SELECT * FROM subscriptions WHERE active = 1"
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def deactivate(sub_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM snapshots WHERE subscription_id = ?", (sub_id,))
        await db.execute("DELETE FROM subscriptions WHERE id = ?", (sub_id,))
        await db.commit()


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
