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
import datetime
import requests
from flask import Flask, request, jsonify

import db

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
CURRENCY = "₦"
# Your own Telegram chat_id (DM @userinfobot to get it). Only this chat_id
# can run /stats. Leave unset and /stats is disabled entirely.
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "")

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


def fmt(amount, currency=None):
    return f"{currency or CURRENCY}{amount:,.0f}"


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
    send_message(chat_id, f"✅ Monthly income set to {fmt(amount, db.get_currency(chat_id))}")


def cmd_setbudget(chat_id, args):
    if len(args) < 2 or not args[-1].replace(".", "", 1).isdigit():
        send_message(chat_id, "Usage: `/setbudget food 50000`")
        return
    amount = float(args[-1])
    category = " ".join(args[:-1])
    db.set_budget(chat_id, category, amount)
    cur = db.get_currency(chat_id)
    send_message(chat_id, f"✅ Budget for *{category.title()}* set to {fmt(amount, cur)}/month")


def cmd_budget(chat_id, args):
    budgets = db.get_budgets(chat_id)
    if not budgets:
        send_message(chat_id, "No budgets set yet. Try `/setbudget food 50000`")
        return
    cur = db.get_currency(chat_id)
    spent = db.get_all_spent_this_month(chat_id)
    lines = ["💰 *My Budget*\n"]
    for cat, amt in budgets.items():
        used = spent.get(cat, 0)
        remaining = amt - used
        lines.append(f"• {cat.title()}: {fmt(used, cur)} / {fmt(amt, cur)} ({fmt(remaining, cur)} left)")
    send_message(chat_id, "\n".join(lines))


def _pace_warning(chat_id, category, budget_amount):
    """Return a warning string if projected spend will blow the budget, else None."""
    if not budget_amount:
        return None
    cur = db.get_currency(chat_id)
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
        f"At this pace, projected month-end spend: {fmt(projected, cur)} "
        f"(budget: {fmt(budget_amount, cur)})"
    )


def _log_expense(chat_id, amount, category, note=""):
    db.add_expense(chat_id, category, amount, note)
    cur = db.get_currency(chat_id)
    spent_month = db.get_category_spent_this_month(chat_id, category)
    budgets = db.get_budgets(chat_id)
    budget_amount = budgets.get(category.lower())

    header = f"✅ Logged {fmt(amount, cur)} → *{category.title()}*"
    if note:
        header += f" _({note})_"
    lines = [header]
    if budget_amount:
        remaining = budget_amount - spent_month
        pct = (spent_month / budget_amount * 100) if budget_amount else 0
        lines.append(f"Monthly spending: {fmt(spent_month, cur)} / {fmt(budget_amount, cur)}")
        lines.append(f"Remaining: {fmt(remaining, cur)} ({pct:.0f}% used)")
    else:
        lines.append(f"Monthly spending: {fmt(spent_month, cur)} (no budget set for this category)")
    send_message(chat_id, "\n".join(lines))

    if budget_amount:
        warning = _pace_warning(chat_id, category, budget_amount)
        if warning:
            send_message(chat_id, warning)


def cmd_spend(chat_id, args):
    if len(args) < 2 or not args[0].replace(".", "", 1).isdigit():
        send_message(chat_id, "Usage: `/spend 2500 food (lunch with friends)`")
        return
    amount = float(args[0])
    category = args[1]
    note = " ".join(args[2:]).strip().strip("()").strip()
    _log_expense(chat_id, amount, category, note)


def cmd_dashboard(chat_id, args):
    cur = db.get_currency(chat_id)
    income = db.get_income(chat_id)
    budgets = db.get_budgets(chat_id)
    spent_by_cat = db.get_all_spent_this_month(chat_id)
    total_spent = sum(spent_by_cat.values())
    remaining = income - total_spent
    day, _, _ = db.month_progress()
    daily_avg = total_spent / day if day else 0

    lines = [
        "📊 *Dashboard*\n",
        f"💰 Income: {fmt(income, cur)}",
        f"💸 Spent: {fmt(total_spent, cur)}",
        f"💵 Remaining: {fmt(remaining, cur)}\n",
    ]
    for cat, amt in budgets.items():
        used = spent_by_cat.get(cat, 0)
        lines.append(f"• {cat.title()} — {fmt(amt - used, cur)} left")
    lines.append(f"\nDaily average: {fmt(daily_avg, cur)}")
    send_message(chat_id, "\n".join(lines))


def cmd_category(chat_id, args):
    if not args:
        send_message(chat_id, "Usage: `/category food`")
        return
    cur = db.get_currency(chat_id)
    category = " ".join(args).lower()
    budgets = db.get_budgets(chat_id)
    budget_amount = budgets.get(category)
    spent = db.get_category_spent_this_month(chat_id, category)

    if budget_amount is None:
        send_message(
            chat_id,
            f"📁 *{category.title()}*\nSpent this month: {fmt(spent, cur)}\n"
            f"No budget set for this category — try `/setbudget {category} 50000`",
        )
        return

    remaining = budget_amount - spent
    pct = (spent / budget_amount * 100) if budget_amount else 0
    lines = [
        f"📁 *{category.title()}*\n",
        f"Budget: {fmt(budget_amount, cur)}/month",
        f"Spent: {fmt(spent, cur)} ({pct:.0f}% used)",
        f"Remaining: {fmt(remaining, cur)}",
    ]
    recent = db.get_recent_expenses(chat_id, category=category, limit=5)
    if recent:
        lines.append("\n_Recent (id — date — amount):_")
        for r in recent:
            entry = f"   [{r['id']}] {r['created_at'][:10]} — {fmt(r['amount'], cur)}"
            if r["note"]:
                entry += f" _({r['note']})_"
            lines.append(entry)
    send_message(chat_id, "\n".join(lines))


def cmd_history(chat_id, args):
    cur = db.get_currency(chat_id)
    rows = db.get_recent_expenses(chat_id, days=7, limit=50)
    if not rows:
        send_message(chat_id, "No spending in the last 7 days.")
        return
    by_day = {}
    for r in rows:
        day = r["created_at"][:10]
        by_day.setdefault(day, []).append(r)
    lines = ["📅 *Last 7 days*\n"]
    for day in sorted(by_day.keys(), reverse=True):
        day_total = sum(r["amount"] for r in by_day[day])
        lines.append(f"*{day}* — {fmt(day_total, cur)}")
        for r in by_day[day]:
            entry = f"   [{r['id']}] {r['category'].title()}: {fmt(r['amount'], cur)}"
            if r["note"]:
                entry += f" _({r['note']})_"
            lines.append(entry)
    send_message(chat_id, "\n".join(lines))


def cmd_savings(chat_id, args):
    cur = db.get_currency(chat_id)
    budgets = db.get_budgets(chat_id)
    savings_budget = budgets.get("savings")
    if savings_budget is None:
        send_message(chat_id, "No savings goal set. Try `/setbudget savings 50000`")
        return
    saved = db.get_category_spent_this_month(chat_id, "savings")
    send_message(
        chat_id,
        f"🎯 *Savings*\nGoal: {fmt(savings_budget, cur)}\nSaved so far: {fmt(saved, cur)}\n"
        f"(log savings with `/spend 20000 savings`)",
    )


def cmd_settings(chat_id, args):
    cur = db.get_currency(chat_id)
    income = db.get_income(chat_id)
    budgets = db.get_budgets(chat_id)
    lines = [
        "⚙️ *Settings*\n",
        f"Monthly income: {fmt(income, cur)} — change with `/income <amount>`",
        f"Currency: {cur} — change with `/currency <symbol>`",
        f"Categories: {', '.join(c.title() for c in budgets) or 'none yet'} — "
        f"add/edit with `/setbudget <category> <amount>`",
        "Reset this month's expenses: `/resetmonth`",
    ]
    send_message(chat_id, "\n".join(lines))


def cmd_resetmonth(chat_id, args):
    db.reset_month(chat_id)
    send_message(chat_id, "🗑️ This month's expenses have been cleared.")


def cmd_undo(chat_id, args):
    cur = db.get_currency(chat_id)
    last = db.get_last_expense(chat_id)
    if not last:
        send_message(chat_id, "No expenses logged yet — nothing to undo.")
        return
    db.delete_expense(chat_id, last["id"])
    send_message(
        chat_id,
        f"↩️ Removed: {fmt(last['amount'], cur)} from *{last['category'].title()}*",
    )


def cmd_edit(chat_id, args):
    if len(args) < 2 or not args[0].isdigit() or not args[1].replace(".", "", 1).isdigit():
        send_message(chat_id, "Usage: `/edit 42 3000` (id from `/history` or `/category`, then new amount)")
        return
    expense_id = int(args[0])
    new_amount = float(args[1])
    cur = db.get_currency(chat_id)
    if db.update_expense_amount(chat_id, expense_id, new_amount):
        send_message(chat_id, f"✏️ Entry [{expense_id}] updated to {fmt(new_amount, cur)}")
    else:
        send_message(chat_id, f"No entry found with id {expense_id}. Check `/history` for ids.")


def cmd_deletebudget(chat_id, args):
    if not args:
        send_message(chat_id, "Usage: `/deletebudget food`")
        return
    category = " ".join(args)
    if db.delete_budget(chat_id, category):
        send_message(chat_id, f"🗑️ Removed budget for *{category.title()}*")
    else:
        send_message(chat_id, f"No budget found for *{category.title()}*")


def cmd_categories(chat_id, args):
    cur = db.get_currency(chat_id)
    budgets = db.get_budgets(chat_id)
    if not budgets:
        send_message(chat_id, "No categories yet. Try `/setbudget food 50000`")
        return
    lines = ["📋 *Categories*\n"]
    for cat, amt in budgets.items():
        lines.append(f"• {cat.title()} — {fmt(amt, cur)}/month")
    send_message(chat_id, "\n".join(lines))


def cmd_top(chat_id, args):
    cur = db.get_currency(chat_id)
    spent_by_cat = db.get_all_spent_this_month(chat_id)
    if not spent_by_cat:
        send_message(chat_id, "No spending logged this month yet.")
        return
    ranked = sorted(spent_by_cat.items(), key=lambda kv: kv[1], reverse=True)[:3]
    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 *Top spending categories this month*\n"]
    for i, (cat, amt) in enumerate(ranked):
        lines.append(f"{medals[i]} {cat.title()} — {fmt(amt, cur)}")
    send_message(chat_id, "\n".join(lines))


def cmd_compare(chat_id, args):
    cur = db.get_currency(chat_id)
    now = datetime.datetime.utcnow()
    this_month_total = db.get_month_total(chat_id, now.year, now.month)
    prev_month = now.month - 1 or 12
    prev_year = now.year if now.month > 1 else now.year - 1
    last_month_total = db.get_month_total(chat_id, prev_year, prev_month)

    lines = [
        "📈 *This month vs last month*\n",
        f"This month so far: {fmt(this_month_total, cur)}",
        f"Last month (full): {fmt(last_month_total, cur)}",
    ]
    if last_month_total:
        diff = this_month_total - last_month_total
        pct = abs(diff) / last_month_total * 100
        if diff > 0:
            lines.append(f"📈 {fmt(diff, cur)} more than last month so far ({pct:.0f}%)")
        elif diff < 0:
            lines.append(f"📉 {fmt(-diff, cur)} less than last month so far ({pct:.0f}%)")
        else:
            lines.append("Exactly even with last month so far.")
    send_message(chat_id, "\n".join(lines))


def cmd_weekly(chat_id, args):
    cur = db.get_currency(chat_id)
    rows = db.get_recent_expenses(chat_id, days=28, limit=1000)
    if not rows:
        send_message(chat_id, "No spending in the last 4 weeks.")
        return
    by_week = {}
    for r in rows:
        d = datetime.datetime.strptime(r["created_at"][:10], "%Y-%m-%d")
        week_start = d - datetime.timedelta(days=d.weekday())
        key = week_start.strftime("%Y-%m-%d")
        by_week[key] = by_week.get(key, 0) + r["amount"]
    lines = ["🗓️ *Last 4 weeks*\n"]
    for week_start in sorted(by_week.keys(), reverse=True):
        lines.append(f"Week of {week_start} — {fmt(by_week[week_start], cur)}")
    send_message(chat_id, "\n".join(lines))


def cmd_currency(chat_id, args):
    if not args or len(args[0]) > 4:
        send_message(chat_id, "Usage: `/currency $` (or ₦, €, £, etc — keep it short)")
        return
    symbol = args[0]
    db.set_currency(chat_id, symbol)
    send_message(chat_id, f"✅ Currency set to {symbol} — amounts will show as {symbol}1,000 from now on")


def cmd_forecast(chat_id, args):
    cur = db.get_currency(chat_id)
    income = db.get_income(chat_id)
    spent_by_cat = db.get_all_spent_this_month(chat_id)
    total_spent = sum(spent_by_cat.values())
    day, days_in_month, fraction_elapsed = db.month_progress()
    projected = (total_spent / day) * days_in_month if day else total_spent

    lines = [
        "🔮 *Month-end forecast*\n",
        f"Spent so far: {fmt(total_spent, cur)} ({fraction_elapsed*100:.0f}% through the month)",
        f"Projected total by month-end: {fmt(projected, cur)}",
    ]
    if income:
        diff = income - projected
        if diff < 0:
            lines.append(f"⚠️ That's {fmt(-diff, cur)} over your {fmt(income, cur)} income at this pace.")
        else:
            lines.append(f"✅ That leaves you {fmt(diff, cur)} under your {fmt(income, cur)} income.")
    send_message(chat_id, "\n".join(lines))


def cmd_export(chat_id, args):
    rows = db.get_all_expenses(chat_id)
    if not rows:
        send_message(chat_id, "No expenses to export yet.")
        return
    lines = ["date,category,amount,note"]
    for r in rows:
        note = (r["note"] or "").replace(",", ";")
        lines.append(f"{r['created_at']},{r['category']},{r['amount']},{note}")
    csv_text = "\n".join(lines)
    requests.post(
        f"{TELEGRAM_API}/sendDocument",
        data={"chat_id": chat_id},
        files={"document": ("spendiabot_export.csv", csv_text, "text/csv")},
        timeout=15,
    )


def cmd_stats(chat_id, args):
    if not ADMIN_CHAT_ID or str(chat_id) != str(ADMIN_CHAT_ID):
        send_message(chat_id, "Unknown command. Try /help")
        return

    s = db.get_usage_stats()
    lines = [
        "📈 *Bot usage*\n",
        f"Total users: {s['total_users']}",
        f"Active today: {s['active_1d']}",
        f"Active last 7 days: {s['active_7d']}",
        f"Active last 30 days: {s['active_30d']}\n",
        "*Top users (by messages sent):*",
    ]
    for u in db.get_active_users(limit=15):
        name = u["username"] or u["first_name"] or str(u["chat_id"])
        last_seen = (u["last_seen"] or "")[:16].replace("T", " ")
        lines.append(f"• {name} — {u['message_count']} msgs (last: {last_seen})")
    send_message(chat_id, "\n".join(lines))


def cmd_help(chat_id, args):
    send_message(
        chat_id,
        "*Commands*\n"
        "/income <amount>\n"
        "/setbudget <category> <amount>\n"
        "/budget\n"
        "/spend <amount> <category> [note]\n"
        "/dashboard\n"
        "/category <name>\n"
        "/history\n"
        "/categories\n"
        "/top\n"
        "/compare\n"
        "/weekly\n"
        "/forecast\n"
        "/undo\n"
        "/edit <id> <amount>\n"
        "/deletebudget <category>\n"
        "/currency <symbol>\n"
        "/resetmonth\n"
        "/export\n\n"
        "Shortcut: just type `2500 food` to log an expense, or `2500 food (lunch)` to add a note.",
    )


COMMANDS = {
    "/start": cmd_start,
    "/income": cmd_income,
    "/setbudget": cmd_setbudget,
    "/budget": cmd_budget,
    "/spend": cmd_spend,
    "/dashboard": cmd_dashboard,
    "/category": cmd_category,
    "/history": cmd_history,
    "/resetmonth": cmd_resetmonth,
    "/undo": cmd_undo,
    "/edit": cmd_edit,
    "/deletebudget": cmd_deletebudget,
    "/categories": cmd_categories,
    "/top": cmd_top,
    "/compare": cmd_compare,
    "/weekly": cmd_weekly,
    "/currency": cmd_currency,
    "/forecast": cmd_forecast,
    "/export": cmd_export,
    "/help": cmd_help,
    "/stats": cmd_stats,
}

BUTTON_MAP = {
    "💰 My Budget": cmd_budget,
    "📊 Dashboard": cmd_dashboard,
    "📅 History": cmd_history,
    "🎯 Savings": cmd_savings,
    "⚙️ Settings": cmd_settings,
}

QUICK_EXPENSE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s+([a-zA-Z][a-zA-Z ]*?)\s*$")
BRACKET_NOTE_RE = re.compile(r"^(.*?)\s*\(([^)]*)\)\s*$")


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

    # "2500 food (lunch with friends)" — strip the bracket note, then parse as usual
    bracket_match = BRACKET_NOTE_RE.match(text)
    if bracket_match:
        stripped, note = bracket_match.group(1).strip(), bracket_match.group(2).strip()
        m = QUICK_EXPENSE_RE.match(stripped)
        if m:
            amount = float(m.group(1))
            category = m.group(2).strip()
            _log_expense(chat_id, amount, category, note)
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
    from_user = message.get("from") or {}
    db.touch_user(chat_id, from_user.get("username"), from_user.get("first_name"))

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
