-- Schema for the finance tracker. Applied idempotently on every startup.

CREATE TABLE IF NOT EXISTS categories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    emoji       TEXT NOT NULL DEFAULT '💰',
    sort_order  INTEGER NOT NULL DEFAULT 0,
    archived    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS category_aliases (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id  INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    alias        TEXT NOT NULL COLLATE NOCASE,
    UNIQUE(category_id, alias)
);

-- period = 'default' for the standing budget, or 'YYYY-MM' for a one-off
-- override of that specific period. Lookups fall back to 'default'.
CREATE TABLE IF NOT EXISTS budgets (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id   INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    period        TEXT NOT NULL DEFAULT 'default',
    amount_cents  INTEGER NOT NULL,
    UNIQUE(category_id, period)
);

-- No period label is stored here on purpose: period boundaries are computed
-- at query time from occurred_on, so changing the cycle day later re-slices
-- history correctly instead of leaving stale labels behind.
CREATE TABLE IF NOT EXISTS expenses (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id    INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    user_id        INTEGER,
    amount_cents   INTEGER NOT NULL,
    description    TEXT NOT NULL DEFAULT '',
    occurred_on    TEXT NOT NULL,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    raw_message    TEXT,
    source         TEXT NOT NULL DEFAULT 'text',
    tg_message_id  INTEGER
);

-- Short-lived state for in-flight confirmation cards (kind='confirm'),
-- just-saved cards that still show an undo button (kind='saved'), and
-- in-flight non-expense command batches awaiting confirmation
-- (kind='command', see bot/actions.py). A table rather than an in-memory
-- dict so a pending card survives a bot restart.
CREATE TABLE IF NOT EXISTS pending (
    token       TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    chat_id     INTEGER NOT NULL,
    user_id     INTEGER,
    message_id  INTEGER,
    payload     TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_expenses_occurred_on ON expenses(occurred_on);
CREATE INDEX IF NOT EXISTS idx_expenses_category ON expenses(category_id);
