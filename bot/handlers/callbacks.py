"""Inline-button handling for confirmation cards (pick category / create
category / save / cancel / edit category) and the post-save undo button."""
from __future__ import annotations

import logging
import secrets
import sqlite3
from datetime import date

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot import cards, db, periods, reports
from bot.money import format_brl

logger = logging.getLogger(__name__)

_EXPIRED = "⏰ Isso expirou ou já foi processado. Envie a mensagem de novo."


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    data = query.data or ""
    config = context.bot_data["config"]
    conn = context.bot_data["conn"]
    user = update.effective_user

    if not config.is_allowed(user.id):
        await query.answer("Não autorizado.", show_alert=True)
        return

    parts = data.split(":")
    action = parts[0]

    if action == "pick" and len(parts) == 3:
        await _handle_pick(query, conn, token=parts[1], category_id=int(parts[2]))
    elif action == "create" and len(parts) == 2:
        await _handle_create(query, conn, token=parts[1])
    elif action == "save" and len(parts) == 2:
        await _handle_save(query, conn, token=parts[1])
    elif action == "cancel" and len(parts) == 2:
        await _handle_cancel(query, conn, token=parts[1])
    elif action == "editcat" and len(parts) == 2:
        await _handle_editcat(query, conn, token=parts[1])
    elif action == "del" and len(parts) == 2:
        await _handle_delete(query, conn, token=parts[1])
    else:
        await query.answer()
        return

    await query.answer()


async def _handle_pick(query, conn, token: str, category_id: int) -> None:
    pending = db.get_pending(conn, token)
    if not pending or pending["kind"] != "confirm":
        await query.edit_message_text(_EXPIRED)
        return
    payload = pending["payload"]
    idx = cards.first_unresolved_index(payload["items"])
    if idx is None:
        return
    payload["items"][idx]["category_id"] = category_id
    db.update_pending_payload(conn, token, payload)
    text, kb = cards.render(conn, token, payload)
    await query.edit_message_text(text, reply_markup=kb)


async def _handle_create(query, conn, token: str) -> None:
    pending = db.get_pending(conn, token)
    if not pending or pending["kind"] != "confirm":
        await query.edit_message_text(_EXPIRED)
        return
    payload = pending["payload"]
    idx = cards.first_unresolved_index(payload["items"])
    if idx is None:
        return
    item = payload["items"][idx]
    guess = (item.get("category_name_guess") or item.get("description") or "Nova categoria").strip()
    name = guess[:1].upper() + guess[1:] if guess else "Nova categoria"
    name = name[:40]
    try:
        category_id = db.create_category(conn, name, "📦")
    except sqlite3.IntegrityError:
        existing = db.find_category(conn, name)
        category_id = existing["id"] if existing else None
    if category_id is None:
        await query.edit_message_text("⚠️ Não consegui criar a categoria. Tente /newcat manualmente.")
        return
    item["category_id"] = category_id
    db.update_pending_payload(conn, token, payload)
    text, kb = cards.render(conn, token, payload)
    await query.edit_message_text(text, reply_markup=kb)


async def _handle_editcat(query, conn, token: str) -> None:
    pending = db.get_pending(conn, token)
    if not pending or pending["kind"] != "confirm":
        await query.edit_message_text(_EXPIRED)
        return
    payload = pending["payload"]
    if len(payload["items"]) != 1:
        return
    payload["items"][0]["category_id"] = None
    db.update_pending_payload(conn, token, payload)
    text, kb = cards.render(conn, token, payload)
    await query.edit_message_text(text, reply_markup=kb)


async def _handle_cancel(query, conn, token: str) -> None:
    pending = db.get_pending(conn, token)
    db.delete_pending(conn, token)
    if not pending:
        await query.edit_message_text(_EXPIRED)
        return
    await query.edit_message_text("❌ Cancelado.")


async def _handle_save(query, conn, token: str) -> None:
    pending = db.get_pending(conn, token)
    if not pending or pending["kind"] != "confirm":
        await query.edit_message_text(_EXPIRED)
        return
    payload = pending["payload"]
    items = payload["items"]
    if cards.first_unresolved_index(items) is not None:
        return  # shouldn't happen -- save button only appears once all resolved

    cycle_day = db.get_cycle_day(conn)
    period = periods.period_containing(date.today(), cycle_day)

    alerts: list[str] = []
    saved_ids: list[int] = []
    saved_lines: list[str] = []
    for it in items:
        cat = db.get_category(conn, it["category_id"])
        spent_before = db.sum_spent(conn, it["category_id"], period.start.isoformat(), period.end.isoformat())
        budget = db.get_budget(conn, it["category_id"])
        eid = db.add_expense(
            conn,
            category_id=it["category_id"],
            amount_cents=it["amount_cents"],
            description=it["description"],
            occurred_on=it["occurred_on"],
            user_id=pending["user_id"],
            raw_message=payload.get("raw_message"),
            source=payload.get("source", "text"),
        )
        saved_ids.append(eid)
        spent_after = spent_before + it["amount_cents"]
        alerts.extend(reports.alert_lines(spent_before, spent_after, budget, cat["name"]))
        saved_lines.append(f"{cat['emoji']} {format_brl(it['amount_cents'])} → {cat['name']}")

    db.delete_pending(conn, token)
    save_token = secrets.token_hex(6)
    db.create_pending(
        conn, save_token, "saved", pending["chat_id"], pending["user_id"], {"expense_ids": saved_ids}
    )

    lines = ["✅ Salvo!", *saved_lines]
    if alerts:
        lines.append("")
        lines.extend(alerts)
    text = "\n".join(lines)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🗑 Apagar", callback_data=f"del:{save_token}")]])
    await query.edit_message_text(text, reply_markup=kb)


async def _handle_delete(query, conn, token: str) -> None:
    pending = db.get_pending(conn, token)
    if not pending or pending["kind"] != "saved":
        await query.edit_message_text("Já processado.")
        return
    ids = pending["payload"]["expense_ids"]
    db.delete_expenses(conn, ids)
    db.delete_pending(conn, token)
    await query.edit_message_text("🗑 Removido.")
