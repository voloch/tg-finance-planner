# tg-finance-planner

A personal Telegram bot for tracking monthly spending against per-category
budgets, in BRL. Send it a message in Portuguese or English ("gastei 50 no
mercado", "spent 120 on gas") or a photo of a receipt, and it uses an LLM
(via OpenRouter) to figure out the category and amount, shows you what it
understood, and — once you confirm — logs it and tells you how much budget
is left for the period.

## Features

- Natural-language expense logging in PT or EN, multiple expenses per message
- Confirms every expense before saving (never writes silently)
- Unrecognized category? Pick from a button list or create it on the spot
- Per-category monthly budgets, reset on a day you choose (`/cycleday`)
- 80% / 100% budget alerts
- `/chart` — spending vs. budget + distribution pie, as an image
- Automatic closing-period report (text + chart) posted on your cycle day
- Receipt photo OCR (vision model reads the total off a nota fiscal/receipt)
- `/undo` and a 🗑 button on every saved entry
- `/export` — CSV of the current period
- All money stored as integer centavos in SQLite; nothing is ever deleted,
  so `/month 2026-07` works for any past period

## Setup

Requires Python 3 (tested on 3.14) and a Telegram bot token (from
[@BotFather](https://t.me/BotFather)) and an [OpenRouter](https://openrouter.ai/keys)
API key.

1. Copy `.env.example` to `.env` and fill in `TELEGRAM_TOKEN` and
   `OPENROUTER_TOKEN`. (If a `.env` already exists in the parent directory
   with these values, the bot will find it automatically — no need to
   duplicate it.)
2. Run it:
   ```bash
   ./run.sh
   ```
   This creates a `.venv/`, installs `requirements.txt` on first run (and
   whenever the file changes), and starts the bot.
3. Open a chat with your bot in Telegram and send `/whoami` to get your
   user ID. Put it in `.env` as `ALLOWED_USER_IDS=123456789` (comma-separate
   for multiple people) and restart the bot. **Until you do this, anyone who
   finds the bot's username can use it.**
4. Create your first categories:
   ```
   /newcat Supermarket 600 🛒
   /newcat Restaurants 500 🍽
   /newcat Fuel 300 ⚡
   /newcat Pharmacy 200 💊
   ```
5. Start logging: just talk to it. "gastei 50 no mercado" or "spent 45 at
   the pharmacy" both work.

## Commands

Run `/help` in the chat for the full list. Highlights:

| Command | What it does |
|---|---|
| `/newcat Name Budget [emoji]` | Create a category |
| `/budget Name Amount` | Set/update a category's budget |
| `/addalias Name alias1 alias2` | Teach extra synonyms for a category |
| `/status` | Current period summary |
| `/month 2026-07` | Summary for a specific month |
| `/chart` | Spending chart image |
| `/cycleday [N]` | View or set the day of the month the budget resets |
| `/undo` | Remove your last logged expense |
| `/export` | CSV of the current period |

Slash-command category names must be a single word (Telegram splits
arguments on whitespace). Multi-word category names work fine through
natural-language logging.

## Running it permanently (systemd user service)

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/tg-finance-planner.service <<EOF
[Unit]
Description=Telegram finance planner bot
After=network-online.target

[Service]
WorkingDirectory=$(pwd)
ExecStart=$(pwd)/run.sh
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now tg-finance-planner
loginctl enable-linger "$USER"   # keeps it running after you log out
journalctl --user -u tg-finance-planner -f   # tail the logs
```

## Data & model

- SQLite database at `data/finance.db` (path configurable via `DB_PATH`).
  Nothing is ever deleted on a period reset — periods are computed at query
  time from each expense's date, so changing `/cycleday` re-slices history
  correctly and past months stay available via `/month`.
- Default model: `deepseek/deepseek-v4-flash-0731` via OpenRouter — cheap
  ($0.065/M input, $0.18/M output tokens) and supports structured JSON
  output. Override with `OPENROUTER_MODEL` in `.env`. Receipt photos use
  `OPENROUTER_VISION_MODEL` (default `deepseek/deepseek-v4-flash-vision-exp`).

## Development

```bash
.venv/bin/python -m pytest tests/ -v
```

Tests cover `bot/money.py` (BRL parsing/formatting) and `bot/periods.py`
(budget-cycle date math) — the two places where a silent bug would be most
expensive. Everything else is exercised by using the bot.
