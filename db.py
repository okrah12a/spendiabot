"""
db.py — all SQLite access for SpendiaBot.

Schema:
  users(chat_id PK, income, currency, created_at)
  budgets(chat_id, category, amount)         -- one row per chat+category
  expenses(id, chat_id, category, amount, note, created_at)

Every "month" is scoped to calendar month (created_at falls in current
month) — simplest possible model for an MVP, no month-rollover table.
"""

import sqlite3
import datetime
import calendar
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "budgetbot.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _add_column_if_missing(conn, table, column, coltype):
    cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            chat_id INTEGER PRIMARY KEY,
            income REAL DEFAULT 0,
            currency TEXT DEFAULT '₦',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Usage-tracking columns (added via migration so existing DBs upgrade in place)
    _add_column_if_missing(conn, "users", "username", "TEXT")
    _add_column_if_missing(conn, "users", "first_name", "TEXT")
    _add_column_if_missing(conn, "users", "last_seen", "TEXT")
    _add_column_if_missing(conn, "users", "message_count", "INTEGER DEFAULT 0")
    conn.commit()
    c.execute("""
        CREATE TABLE IF NOT EXISTS budgets (
            chat_id INTEGER,
            category TEXT,
            amount REAL,
            PRIMARY KEY (chat_id, category)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            category TEXT,
            amount REAL,
            note TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


def ensure_user(chat_id):
    conn = get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO users (chat_id) VALUES (?)", (chat_id,)
    )
    conn.commit()
    conn.close()


def set_income(chat_id, amount):
    ensure_user(chat_id)
    conn = get_conn()
    conn.execute("UPDATE users SET income = ? WHERE chat_id = ?", (amount, chat_id))
    conn.commit()
    conn.close()


def get_income(chat_id):
    conn = get_conn()
    row = conn.execute("SELECT income FROM users WHERE chat_id = ?", (chat_id,)).fetchone()
    conn.close()
    return row["income"] if row else 0


def set_budget(chat_id, category, amount):
    ensure_user(chat_id)
    conn = get_conn()
    conn.execute(
        "INSERT INTO budgets (chat_id, category, amount) VALUES (?, ?, ?) "
        "ON CONFLICT(chat_id, category) DO UPDATE SET amount = excluded.amount",
        (chat_id, category.lower(), amount),
    )
    conn.commit()
    conn.close()


def get_budgets(chat_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT category, amount FROM budgets WHERE chat_id = ? ORDER BY category",
        (chat_id,),
    ).fetchall()
    conn.close()
    return {r["category"]: r["amount"] for r in rows}


def add_expense(chat_id, category, amount, note=""):
    ensure_user(chat_id)
    conn = get_conn()
    conn.execute(
        "INSERT INTO expenses (chat_id, category, amount, note) VALUES (?, ?, ?, ?)",
        (chat_id, category.lower(), amount, note),
    )
    conn.commit()
    conn.close()


def _month_bounds():
    now = datetime.datetime.utcnow()
    start = datetime.datetime(now.year, now.month, 1)
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    return start, now, now.day, days_in_month


def get_category_spent_this_month(chat_id, category):
    start, now, _, _ = _month_bounds()
    conn = get_conn()
    row = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM expenses "
        "WHERE chat_id = ? AND category = ? AND created_at >= ?",
        (chat_id, category.lower(), start.isoformat()),
    ).fetchone()
    conn.close()
    return row["total"]


def get_all_spent_this_month(chat_id):
    start, _, _, _ = _month_bounds()
    conn = get_conn()
    rows = conn.execute(
        "SELECT category, COALESCE(SUM(amount), 0) AS total FROM expenses "
        "WHERE chat_id = ? AND created_at >= ? GROUP BY category",
        (chat_id, start.isoformat()),
    ).fetchall()
    conn.close()
    return {r["category"]: r["total"] for r in rows}


def get_spent_today(chat_id):
    today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    conn = get_conn()
    rows = conn.execute(
        "SELECT category, COALESCE(SUM(amount), 0) AS total FROM expenses "
        "WHERE chat_id = ? AND date(created_at) = ? GROUP BY category",
        (chat_id, today),
    ).fetchall()
    conn.close()
    return {r["category"]: r["total"] for r in rows}


def get_history(chat_id, days=7):
    since = (datetime.datetime.utcnow() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    conn = get_conn()
    rows = conn.execute(
        "SELECT date(created_at) AS day, category, SUM(amount) AS total FROM expenses "
        "WHERE chat_id = ? AND date(created_at) >= ? GROUP BY day, category ORDER BY day DESC",
        (chat_id, since),
    ).fetchall()
    conn.close()
    return rows


def month_progress():
    """Returns (day_of_month, days_in_month, fraction_elapsed)."""
    _, _, day, days_in_month = _month_bounds()
    return day, days_in_month, day / days_in_month


def get_currency(chat_id):
    conn = get_conn()
    row = conn.execute("SELECT currency FROM users WHERE chat_id = ?", (chat_id,)).fetchone()
    conn.close()
    return row["currency"] if row and row["currency"] else "₦"


def set_currency(chat_id, symbol):
    ensure_user(chat_id)
    conn = get_conn()
    conn.execute("UPDATE users SET currency = ? WHERE chat_id = ?", (symbol, chat_id))
    conn.commit()
    conn.close()


def update_expense_amount(chat_id, expense_id, new_amount):
    conn = get_conn()
    cur = conn.execute(
        "UPDATE expenses SET amount = ? WHERE chat_id = ? AND id = ?",
        (new_amount, chat_id, expense_id),
    )
    conn.commit()
    updated = cur.rowcount > 0
    conn.close()
    return updated


def get_month_total(chat_id, year, month):
    start = datetime.datetime(year, month, 1)
    if month == 12:
        end = datetime.datetime(year + 1, 1, 1)
    else:
        end = datetime.datetime(year, month + 1, 1)
    conn = get_conn()
    row = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM expenses "
        "WHERE chat_id = ? AND created_at >= ? AND created_at < ?",
        (chat_id, start.isoformat(), end.isoformat()),
    ).fetchone()
    conn.close()
    return row["total"]


def get_last_expense(chat_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT id, category, amount, created_at FROM expenses "
        "WHERE chat_id = ? ORDER BY id DESC LIMIT 1",
        (chat_id,),
    ).fetchone()
    conn.close()
    return row


def delete_expense(chat_id, expense_id):
    conn = get_conn()
    conn.execute("DELETE FROM expenses WHERE chat_id = ? AND id = ?", (chat_id, expense_id))
    conn.commit()
    conn.close()


def delete_budget(chat_id, category):
    conn = get_conn()
    cur = conn.execute(
        "DELETE FROM budgets WHERE chat_id = ? AND category = ?", (chat_id, category.lower())
    )
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def get_recent_expenses(chat_id, category=None, days=None, limit=10):
    """Individual expense rows (not aggregated), newest first — used to show
    notes/descriptions next to each entry."""
    query = "SELECT id, category, amount, note, created_at FROM expenses WHERE chat_id = ?"
    params = [chat_id]
    if category:
        query += " AND category = ?"
        params.append(category.lower())
    if days:
        since = (datetime.datetime.utcnow() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
        query += " AND date(created_at) >= ?"
        params.append(since)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    conn = get_conn()
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return rows


def get_all_expenses(chat_id):
    """All-time expense rows, oldest first — used for CSV export."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, category, amount, note, created_at FROM expenses "
        "WHERE chat_id = ? ORDER BY created_at ASC",
        (chat_id,),
    ).fetchall()
    conn.close()
    return rows


def touch_user(chat_id, username=None, first_name=None):
    """Call this on every incoming message. Upserts the user, stamps last_seen
    'now', and increments their lifetime message_count."""
    ensure_user(chat_id)
    conn = get_conn()
    conn.execute(
        "UPDATE users SET username = ?, first_name = ?, last_seen = ?, "
        "message_count = COALESCE(message_count, 0) + 1 WHERE chat_id = ?",
        (username, first_name, datetime.datetime.utcnow().isoformat(), chat_id),
    )
    conn.commit()
    conn.close()


def get_usage_stats():
    """High-level counts for a /stats command: total users, and how many
    were active in the last 1 / 7 / 30 days."""
    now = datetime.datetime.utcnow()
    conn = get_conn()
    total = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]

    def active_since(days):
        cutoff = (now - datetime.timedelta(days=days)).isoformat()
        return conn.execute(
            "SELECT COUNT(*) AS n FROM users WHERE last_seen >= ?", (cutoff,)
        ).fetchone()["n"]

    stats = {
        "total_users": total,
        "active_1d": active_since(1),
        "active_7d": active_since(7),
        "active_30d": active_since(30),
    }
    conn.close()
    return stats


def get_active_users(limit=20):
    """Most active users by lifetime message_count, most recently seen first
    as a tiebreaker. Good for a leaderboard-style /stats view."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT chat_id, username, first_name, message_count, last_seen, created_at "
        "FROM users ORDER BY message_count DESC, last_seen DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return rows


def reset_month(chat_id):
    start, _, _, _ = _month_bounds()
    conn = get_conn()
    conn.execute(
        "DELETE FROM expenses WHERE chat_id = ? AND created_at >= ?",
        (chat_id, start.isoformat()),
    )
    conn.commit()
    conn.close()
