# SpendiaBot

A free Telegram bot that tracks income, category budgets, and daily spending —
and warns you when you're burning through a category faster than your pace
through the month allows. No AI, no paid services.

## 1. Create the bot (2 minutes, free)

1. Open Telegram, message **[@BotFather](https://t.me/BotFather)**
2. Send `/newbot`, follow the prompts, pick a name and a username ending in `bot`
3. BotFather gives you a **token** like `123456789:ABCdefGhIJKlmNoPQRstuVwxyZ` — save it

## 2. Push this code to GitHub (free)

```bash
cd budgetbot
git init
git add .
git commit -m "Initial SpendiaBot"
gh repo create budgetbot --public --source=. --push
# or create a repo on github.com and: git remote add origin <url> && git push -u origin main
```

## 3. Deploy on Render (free)

1. Go to [render.com](https://render.com) → New → **Web Service**
2. Connect your GitHub repo
3. Settings:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app`
   - **Instance type:** Free
4. Add an environment variable:
   - `BOT_TOKEN` = the token from BotFather
5. Deploy. Render gives you a URL like `https://budgetbot-xxxx.onrender.com`

Note: the free tier **spins down after 15 minutes idle** and takes ~30-50
seconds to wake back up. That's fine here — Telegram just waits a bit longer
for the first reply after a quiet spell, and there's no cost to you.

## 4. Point Telegram at your bot (one-time)

Run this once, replacing both placeholders:

```bash
curl "https://api.telegram.org/bot<BOT_TOKEN>/setWebhook?url=https://<your-render-url>/webhook/<BOT_TOKEN>"
```

You should get `{"ok":true,"result":true,...}` back.

## 5. Use it

Open your bot in Telegram and send `/start`. Then:

```
/income 250000
/setbudget food 50000
/setbudget transport 30000
/setbudget savings 50000
2500 food          <- quick-log shortcut, or /spend 2500 food
/dashboard
/history
```

## Storage

Everything lives in a local SQLite file (`budgetbot.db`) on the Render
instance. This is fine for an MVP but **the free tier's disk isn't
guaranteed to persist across redeploys** — if that becomes a problem, the
easiest free upgrade path is Render's free PostgreSQL tier or a hosted
SQLite service like Turso (both have generous free tiers and only need a
few line changes in `db.py`).

## What's already built

- Multi-user (each chat_id is its own budget/user — group chats work too)
- Monthly income + per-category budgets
- Expense logging via command or free text (`2500 food`)
- Dashboard, 7-day history, savings tracking
- Pace-based overspend warnings (compares % of budget used vs % of month elapsed)
- `/resetmonth` to clear the current month's expenses

## Natural next steps (still free)

- Reply-keyboard buttons already wired up (💰📊📅🎯⚙️) — could extend to inline buttons for category picking
- Recurring monthly reminder (`/income` nudge on the 1st) via Render Cron Jobs (free)
- Export history to CSV
