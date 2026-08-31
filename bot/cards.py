"""Renders the confirmation-card message + inline keyboard for a pending
expense-log token, shared by the text handler (which creates it) and the
callback handler (which re-renders it after each button tap)."""
from __future__ import annotations

from datetime import date

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot import db, periods
from bot.money import format_brl


def first_unresolved_index(items: list[dict]) -> int | None:
    for i, it in enumerate(items):
        if it.get("category_id") is None:
            return i
    return None


def build_category_picker(token: str, categories: list[dict], name_guess: str | None) -> InlineKeyboardMarkup:
    rows = []
    row: list[InlineKeyboardButton] = []
    for c in categories:
        row.append(InlineKeyboardButton(f"{c['emoji']} {c['name']}", callback_data=f"pick:{token}:{c['id']}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    if name_guess:
        label = name_guess if len(name_guess) <= 24 else name_guess[:21] + "..."
        rows.append([InlineKeyboardButton(f"➕ Criar «{label}»", callback_data=f"create:{token}")])
    rows.append([InlineKeyboardButton("❌ Cancelar", callback_data=f"cancel:{token}")])
    return InlineKeyboardMarkup(rows)


def render(conn, token: str, payload: dict) -> tuple[str, InlineKeyboardMarkup]:
    items = payload["items"]
    idx = first_unresolved_index(items)

    if idx is not None:
        it = items[idx]
        categories = db.list_categories_with_aliases(conn)
        what = it.get("description") or format_brl(it["amount_cents"])
        n = len(items)
        prefix = f"({idx + 1}/{n}) " if n > 1 else ""
        text = f"❓ {prefix}Não reconheci a categoria para \"{what}\" ({format_brl(it['amount_cents'])}).\nEscolha uma:"
        kb = build_category_picker(token, categories, it.get("category_name_guess"))
        return text, kb

    cycle_day = db.get_cycle_day(conn)
    period = periods.period_containing(date.today(), cycle_day)

    lines = ["🧾"]
    for it in items:
        cat = db.get_category(conn, it["category_id"])
        spent_before = db.sum_spent(conn, it["category_id"], period.start.isoformat(), period.end.isoformat())
        budget = db.get_budget(conn, it["category_id"])
        desc = it.get("description") or "-"
        occurred = it["occurred_on"]
        today_str = date.today().isoformat()
        when = "hoje" if occurred == today_str else occurred
        line = f"{cat['emoji']} {format_brl(it['amount_cents'])} → {cat['name']}\n\"{desc}\" · {when}"
        if budget:
            remaining = budget - spent_before - it["amount_cents"]
            line += f"\nRestam {format_brl(remaining)} de {format_brl(budget)}"
        else:
            line += "\nsem orçamento definido"
        lines.append(line)
    text = "\n\n".join(lines)

    buttons = [[InlineKeyboardButton("✅ Salvar" if len(items) == 1 else "✅ Salvar tudo", callback_data=f"save:{token}")]]
    if len(items) == 1:
        buttons[0].append(InlineKeyboardButton("✏️ Categoria", callback_data=f"editcat:{token}"))
    buttons.append([InlineKeyboardButton("❌ Cancelar", callback_data=f"cancel:{token}")])
    return text, InlineKeyboardMarkup(buttons)


def render_command_card(token: str, payload: dict) -> tuple[str, InlineKeyboardMarkup]:
    """Renders a confirmation card for one or more write commands (created by
    bot/handlers/text.py._handle_commands). Unlike `render` above, this needs
    no live `conn` -- the command lines and plain-language descriptions are
    computed once at card-creation time and stored in the payload, so what
    the user approves is exactly what gets executed."""
    calls = payload["calls"]
    header = "⚙️ Vou executar:" if len(calls) == 1 else f"⚙️ Vou executar {len(calls)} comandos:"
    lines = [header, "", "\n".join(c["display"] for c in calls)]
    descriptions = [c["describe"] for c in calls if c.get("describe")]
    if descriptions:
        lines.append("")
        lines.append("\n".join(descriptions))
    text = "\n".join(lines)

    run_label = "✅ Executar" if len(calls) == 1 else "✅ Executar tudo"
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(run_label, callback_data=f"run:{token}")],
            [InlineKeyboardButton("❌ Cancelar", callback_data=f"cancel:{token}")],
        ]
    )
    return text, kb
