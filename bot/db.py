"""All sqlite access lives here. Plain stdlib sqlite3 -- the schema is small
enough to stay inspectable with `sqlite3 data/finance.db`."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

DEFAULT_CYCLE_DAY = 1


def get_conn(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text())
    conn.commit()


# ---------------------------------------------------------------- settings --

def get_setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def get_cycle_day(conn: sqlite3.Connection) -> int:
    return int(get_setting(conn, "cycle_day", str(DEFAULT_CYCLE_DAY)))


def get_home_chat_id(conn: sqlite3.Connection) -> int | None:
    v = get_setting(conn, "home_chat_id")
    return int(v) if v else None


def set_home_chat_id(conn: sqlite3.Connection, chat_id: int) -> None:
    set_setting(conn, "home_chat_id", str(chat_id))


# -------------------------------------------------------------- categories --

def list_categories(conn: sqlite3.Connection, include_archived: bool = False) -> list[sqlite3.Row]:
    q = "SELECT * FROM categories"
    if not include_archived:
        q += " WHERE archived = 0"
    q += " ORDER BY sort_order, name"
    return conn.execute(q).fetchall()


def list_categories_with_aliases(conn: sqlite3.Connection) -> list[dict]:
    cats = list_categories(conn)
    out = []
    for c in cats:
        aliases = [
            r["alias"]
            for r in conn.execute(
                "SELECT alias FROM category_aliases WHERE category_id = ?", (c["id"],)
            ).fetchall()
        ]
        out.append({"id": c["id"], "name": c["name"], "emoji": c["emoji"], "aliases": aliases})
    return out


def get_category(conn: sqlite3.Connection, category_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM categories WHERE id = ?", (category_id,)).fetchone()


def find_category(conn: sqlite3.Connection, name_or_alias: str | None) -> sqlite3.Row | None:
    """Case-insensitive lookup by exact category name, then by alias."""
    if not name_or_alias:
        return None
    row = conn.execute(
        "SELECT * FROM categories WHERE name = ? COLLATE NOCASE AND archived = 0", (name_or_alias,)
    ).fetchone()
    if row:
        return row
    row = conn.execute(
        """
        SELECT c.* FROM categories c
        JOIN category_aliases a ON a.category_id = c.id
        WHERE a.alias = ? COLLATE NOCASE AND c.archived = 0
        """,
        (name_or_alias,),
    ).fetchone()
    return row


def create_category(
    conn: sqlite3.Connection, name: str, emoji: str = "💰", budget_cents: int | None = None
) -> int:
    cur = conn.execute(
        "INSERT INTO categories(name, emoji, sort_order) VALUES (?, ?, "
        "(SELECT COALESCE(MAX(sort_order), 0) + 1 FROM categories))",
        (name, emoji),
    )
    category_id = cur.lastrowid
    if budget_cents is not None:
        set_budget(conn, category_id, budget_cents)
    conn.commit()
    return category_id


def add_alias(conn: sqlite3.Connection, category_id: int, alias: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO category_aliases(category_id, alias) VALUES (?, ?)",
        (category_id, alias),
    )
    conn.commit()


def rename_category(conn: sqlite3.Connection, category_id: int, new_name: str) -> None:
    conn.execute("UPDATE categories SET name = ? WHERE id = ?", (new_name, category_id))
    conn.commit()


def archive_category(conn: sqlite3.Connection, category_id: int) -> None:
    conn.execute("UPDATE categories SET archived = 1 WHERE id = ?", (category_id,))
    conn.commit()


# ------------------------------------------------------------------ budgets --

def set_budget(
    conn: sqlite3.Connection, category_id: int, amount_cents: int, period: str = "default"
) -> None:
    conn.execute(
        "INSERT INTO budgets(category_id, period, amount_cents) VALUES (?, ?, ?) "
        "ON CONFLICT(category_id, period) DO UPDATE SET amount_cents = excluded.amount_cents",
        (category_id, period, amount_cents),
    )
    conn.commit()


def get_budget(conn: sqlite3.Connection, category_id: int, period_key: str | None = None) -> int | None:
    """Falls back to the standing 'default' budget if no override exists
    for the given period_key (e.g. '2026-08')."""
    if period_key:
        row = conn.execute(
            "SELECT amount_cents FROM budgets WHERE category_id = ? AND period = ?",
            (category_id, period_key),
        ).fetchone()
        if row:
            return row["amount_cents"]
    row = conn.execute(
        "SELECT amount_cents FROM budgets WHERE category_id = ? AND period = 'default'",
        (category_id,),
    ).fetchone()
    return row["amount_cents"] if row else None


# ----------------------------------------------------------------- expenses --

def add_expense(
    conn: sqlite3.Connection,
    category_id: int,
    amount_cents: int,
    description: str,
    occurred_on: str,
    user_id: int | None = None,
    raw_message: str | None = None,
    source: str = "text",
    tg_message_id: int | None = None,
) -> int:
    cur = conn.execute(
        """INSERT INTO expenses(category_id, user_id, amount_cents, description, occurred_on,
                                 raw_message, source, tg_message_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (category_id, user_id, amount_cents, description, occurred_on, raw_message, source, tg_message_id),
    )
    conn.commit()
    return cur.lastrowid


def delete_expenses(conn: sqlite3.Connection, expense_ids: list[int]) -> None:
    if not expense_ids:
        return
    qmarks = ",".join("?" for _ in expense_ids)
    conn.execute(f"DELETE FROM expenses WHERE id IN ({qmarks})", expense_ids)
    conn.commit()


def get_last_expense(conn: sqlite3.Connection, user_id: int | None) -> sqlite3.Row | None:
    if user_id is not None:
        row = conn.execute(
            "SELECT * FROM expenses WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,)
        ).fetchone()
        if row:
            return row
    return conn.execute("SELECT * FROM expenses ORDER BY id DESC LIMIT 1").fetchone()


def sum_spent(conn: sqlite3.Connection, category_id: int, start: str, end: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) AS total FROM expenses "
        "WHERE category_id = ? AND occurred_on >= ? AND occurred_on < ?",
        (category_id, start, end),
    ).fetchone()
    return row["total"]


def list_expenses(
    conn: sqlite3.Connection,
    start: str | None = None,
    end: str | None = None,
    category_id: int | None = None,
    limit: int = 20,
) -> list[sqlite3.Row]:
    q = """SELECT e.*, c.name AS category_name, c.emoji AS category_emoji
           FROM expenses e JOIN categories c ON c.id = e.category_id WHERE 1=1"""
    params: list = []
    if start:
        q += " AND e.occurred_on >= ?"
        params.append(start)
    if end:
        q += " AND e.occurred_on < ?"
        params.append(end)
    if category_id:
        q += " AND e.category_id = ?"
        params.append(category_id)
    q += " ORDER BY e.occurred_on DESC, e.id DESC LIMIT ?"
    params.append(limit)
    return conn.execute(q, params).fetchall()


# ------------------------------------------------------------------ pending --

def create_pending(
    conn: sqlite3.Connection,
    token: str,
    kind: str,
    chat_id: int,
    user_id: int | None,
    payload: dict,
    message_id: int | None = None,
) -> None:
    conn.execute(
        "INSERT INTO pending(token, kind, chat_id, user_id, message_id, payload) VALUES (?, ?, ?, ?, ?, ?)",
        (token, kind, chat_id, user_id, message_id, json.dumps(payload)),
    )
    conn.commit()


def get_pending(conn: sqlite3.Connection, token: str) -> dict | None:
    row = conn.execute("SELECT * FROM pending WHERE token = ?", (token,)).fetchone()
    if not row:
        return None
    return {
        "token": row["token"],
        "kind": row["kind"],
        "chat_id": row["chat_id"],
        "user_id": row["user_id"],
        "message_id": row["message_id"],
        "payload": json.loads(row["payload"]),
    }


def update_pending_payload(conn: sqlite3.Connection, token: str, payload: dict) -> None:
    conn.execute("UPDATE pending SET payload = ? WHERE token = ?", (json.dumps(payload), token))
    conn.commit()


def set_pending_message_id(conn: sqlite3.Connection, token: str, message_id: int) -> None:
    conn.execute("UPDATE pending SET message_id = ? WHERE token = ?", (message_id, token))
    conn.commit()


def delete_pending(conn: sqlite3.Connection, token: str) -> None:
    conn.execute("DELETE FROM pending WHERE token = ?", (token,))
    conn.commit()


def cleanup_old_pending(conn: sqlite3.Connection, older_than_hours: int = 24) -> None:
    conn.execute(
        f"DELETE FROM pending WHERE created_at < datetime('now', '-{int(older_than_hours)} hours')"
    )
    conn.commit()
