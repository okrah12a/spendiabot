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


def reset_month(chat_id):
    start, _, _, _ = _month_bounds()
    conn = get_conn()
    conn.execute(
        "DELETE FROM expenses WHERE chat_id = ? AND created_at >= ?",
        (chat_id, start.isoformat()),
    )
    conn.commit()
    conn.close()
