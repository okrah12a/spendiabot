"""
app.py — SpendiaBot webhook server.

Runs as a Flask app. Telegram POSTs each incoming message to /webhook/<TOKEN>.
Deployed on Render free tier: the service sleeps when idle and wakes on the
next incoming request, which is fine for a webhook (Telegram just waits a
few seconds longer for the first reply after idle time).

No AI calls, no external paid services — just SQLite + arithmetic.
"""

import os
import re
import requests
from flask import Flask, request, jsonify

import db

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
CURRENCY = "₦"

app = Flask(__name__)
db.init_db()

MENU_KEYBOARD = {
    "keyboard": [
        ["💰 My Budget", "➕ Add Expense"],
        ["📊 Dashboard", "📅 History"],
        ["🎯 Savings", "⚙️ Settings"],
    ],
    "resize_keyboard": True,
}


def send_message(chat_id, text, keyboard=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if keyboard:
        payload["reply_markup"] = keyboard
    requests.post(f"{TELEGRAM_API}/sendMessage", json=payload, timeout=10)


def fmt(amount):
    return f"{CURRENCY}{amount:,.0f}"


# ---------------------------------------------------------------- commands

def cmd_start(chat_id, args):
    db.ensure_user(chat_id)
    send_message(
        chat_id,
        "👋 Welcome to *SpendiaBot*!\n\n"
        "Quick setup:\n"
        "1️⃣ `/income 250000` — set your monthly income\n"
        "2️⃣ `/setbudget food 50000` — set a category budget (repeat per category)\n"
        "3️⃣ Log spending any time with `/spend 2500 food` or just type `2500 food`\n\n"
        "Then use the menu below any time.",
        keyboard=MENU_KEYBOARD,
    )


def cmd_income(chat_id, args):
    if not args or not args[0].replace(".", "", 1).isdigit():
        send_message(chat_id, "Usage: `/income 250000`")
        return
    amount = float(args[0])
    db.set_income(chat_id, amount)
    send_message(chat_id, f"✅ Monthly income set to {fmt(amount)}")


def cmd_setbudget(chat_id, args):
    if len(args) < 2 or not args[-1].replace(".", "", 1).isdigit():
        send_message(chat_id, "Usage: `/setbudget food 50000`")
        return
    amount = float(args[-1])
    category = " ".join(args[:-1])
    db.set_budget(chat_id, category, amount)
    send_message(chat_id, f"✅ Budget for *{category.title()}* set to {fmt(amount)}/month")


def cmd_budget(chat_id, args):
    budgets = db.get_budgets(chat_id)
    if not budgets:
        send_message(chat_id, "No budgets set yet. Try `/setbudget food 50000`")
        return
    spent = db.get_all_spent_this_month(chat_id)
    lines = ["💰 *My Budget*\n"]
    for cat, amt in budgets.items():
        used = spent.get(cat, 0)
        remaining = amt - used
        lines.append(f"• {cat.title()}: {fmt(used)} / {fmt(amt)} ({fmt(remaining)} left)")
    send_message(chat_id, "\n".join(lines))


def _pace_warning(chat_id, category, budget_amount):
    """Return a warning string if projected spend will blow the budget, else None."""
    if not budget_amount:
        return None
    day, days_in_month, fraction_elapsed = db.month_progress()
    spent = db.get_category_spent_this_month(chat_id, category)
    used_fraction = spent / budget_amount
    if used_fraction <= fraction_elapsed + 0.05:
        return None  # roughly on pace or under — no warning
    projected = (spent / day) * days_in_month if day else spent
    return (
        f"⚠️ *{category.title()} budget warning*\n"
        f"You've used {used_fraction*100:.0f}% of this budget, "
        f"but you're only {fraction_elapsed*100:.0f}% through the month.\n"
        f"At this pace, projected month-end spend: {fmt(projected)} "
        f"(budget: {fmt(budget_amount)})"
    )


def _log_expense(chat_id, amount, category, note=""):
    db.add_expense(chat_id, category, amount, note)
    spent_month = db.get_category_spent_this_month(chat_id, category)
    budgets = db.get_budgets(chat_id)
    budget_amount = budgets.get(category.lower())

    lines = [f"✅ Logged {fmt(amount)} → *{category.title()}*"]
    if budget_amount:
        remaining = budget_amount - spent_month
        pct = (spent_month / budget_amount * 100) if budget_amount else 0
        lines.append(f"Monthly spending: {fmt(spent_month)} / {fmt(budget_amount)}")
        lines.append(f"Remaining: {fmt(remaining)} ({pct:.0f}% used)")
    else:
        lines.append(f"Monthly spending: {fmt(spent_month)} (no budget set for this category)")
    send_message(chat_id, "\n".join(lines))

    if budget_amount:
        warning = _pace_warning(chat_id, category, budget_amount)
        if warning:
            send_message(chat_id, warning)


def cmd_spend(chat_id, args):
    if len(args) < 2 or not args[0].replace(".", "", 1).isdigit():
        send_message(chat_id, "Usage: `/spend 2500 food` (optionally add a note after)")
        return
    amount = float(args[0])
    category = args[1]
    note = " ".join(args[2:])
    _log_expense(chat_id, amount, category, note)


def cmd_dashboard(chat_id, args):
    income = db.get_income(chat_id)
    budgets = db.get_budgets(chat_id)
    spent_by_cat = db.get_all_spent_this_month(chat_id)
    total_spent = sum(spent_by_cat.values())
    remaining = income - total_spent
    day, _, _ = db.month_progress()
    daily_avg = total_spent / day if day else 0

    lines = [
        "📊 *Dashboard*\n",
        f"💰 Income: {fmt(income)}",
        f"💸 Spent: {fmt(total_spent)}",
        f"💵 Remaining: {fmt(remaining)}\n",
    ]
    for cat, amt in budgets.items():
        used = spent_by_cat.get(cat, 0)
        lines.append(f"• {cat.title()} — {fmt(amt - used)} left")
    lines.append(f"\nDaily average: {fmt(daily_avg)}")
    send_message(chat_id, "\n".join(lines))


def cmd_history(chat_id, args):
    rows = db.get_history(chat_id, days=7)
    if not rows:
        send_message(chat_id, "No spending in the last 7 days.")
        return
    by_day = {}
    for r in rows:
        by_day.setdefault(r["day"], []).append((r["category"], r["total"]))
    lines = ["📅 *Last 7 days*\n"]
    for day in sorted(by_day.keys(), reverse=True):
        day_total = sum(t for _, t in by_day[day])
        lines.append(f"*{day}* — {fmt(day_total)}")
        for cat, total in by_day[day]:
            lines.append(f"   {cat.title()}: {fmt(total)}")
    send_message(chat_id, "\n".join(lines))


def cmd_savings(chat_id, args):
    budgets = db.get_budgets(chat_id)
    savings_budget = budgets.get("savings")
    if savings_budget is None:
        send_message(chat_id, "No savings goal set. Try `/setbudget savings 50000`")
        return
    saved = db.get_category_spent_this_month(chat_id, "savings")
    send_message(
        chat_id,
        f"🎯 *Savings*\nGoal: {fmt(savings_budget)}\nSaved so far: {fmt(saved)}\n"
        f"(log savings with `/spend 20000 savings`)",
    )


def cmd_settings(chat_id, args):
    income = db.get_income(chat_id)
    budgets = db.get_budgets(chat_id)
    lines = [
        "⚙️ *Settings*\n",
        f"Monthly income: {fmt(income)} — change with `/income <amount>`",
        f"Categories: {', '.join(c.title() for c in budgets) or 'none yet'} — "
        f"add/edit with `/setbudget <category> <amount>`",
        "Reset this month's expenses: `/resetmonth`",
    ]
    send_message(chat_id, "\n".join(lines))


def cmd_resetmonth(chat_id, args):
    db.reset_month(chat_id)
    send_message(chat_id, "🗑️ This month's expenses have been cleared.")


def cmd_help(chat_id, args):
    send_message(
        chat_id,
        "*Commands*\n"
        "/income <amount>\n"
        "/setbudget <category> <amount>\n"
        "/budget\n"
        "/spend <amount> <category> [note]\n"
        "/dashboard\n"
        "/history\n"
        "/resetmonth\n\n"
        "Shortcut: just type `2500 food` to log an expense.",
    )


COMMANDS = {
    "/start": cmd_start,
    "/income": cmd_income,
    "/setbudget": cmd_setbudget,
    "/budget": cmd_budget,
    "/spend": cmd_spend,
    "/dashboard": cmd_dashboard,
    "/history": cmd_history,
    "/resetmonth": cmd_resetmonth,
    "/help": cmd_help,
}

BUTTON_MAP = {
    "💰 My Budget": cmd_budget,
    "📊 Dashboard": cmd_dashboard,
    "📅 History": cmd_history,
    "🎯 Savings": cmd_savings,
    "⚙️ Settings": cmd_settings,
}

QUICK_EXPENSE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s+([a-zA-Z][a-zA-Z ]*)\s*$")


def handle_text(chat_id, text):
    text = text.strip()

    if text in BUTTON_MAP:
        BUTTON_MAP[text](chat_id, [])
        return

    if text == "➕ Add Expense":
        send_message(chat_id, "Type it like this: `2500 food` or `/spend 2500 food`")
        return

    if text.startswith("/"):
        parts = text.split()
        cmd = parts[0].split("@")[0]  # strip @BotName if present
        args = parts[1:]
        handler = COMMANDS.get(cmd)
        if handler:
            handler(chat_id, args)
        else:
            send_message(chat_id, "Unknown command. Try /help")
        return

    m = QUICK_EXPENSE_RE.match(text)
    if m:
        amount = float(m.group(1))
        category = m.group(2).strip()
        _log_expense(chat_id, amount, category)
        return

    send_message(chat_id, "Not sure what that means. Try /help or the menu below.", keyboard=MENU_KEYBOARD)


# ---------------------------------------------------------------- webhook

@app.route(f"/webhook/{BOT_TOKEN}", methods=["POST"])
def webhook():
    update = request.get_json(force=True, silent=True) or {}
    message = update.get("message") or update.get("edited_message")
    if not message:
        return jsonify(ok=True)

    chat_id = message["chat"]["id"]
    text = message.get("text", "")
    if text:
        try:
            handle_text(chat_id, text)
        except Exception as e:
            send_message(chat_id, f"⚠️ Something went wrong: {e}")
    return jsonify(ok=True)


@app.route("/", methods=["GET"])
def health():
    return "SpendiaBot is running."


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
